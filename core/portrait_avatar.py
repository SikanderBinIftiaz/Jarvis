"""
Photo-real animated portrait avatar -- a drop-in replacement for HoloAvatar.

It animates ONE still image (core/jarvis_face.png) with nothing but QPainter:

  * natural blinking   -- random timing, occasional double blink, eyelids are
                          rebuilt from the face's own skin colour, lash line
                          drawn on top, eyes close slowly when asleep
  * talking            -- the lower lip and chin are warped down and a mouth
                          interior (teeth + tongue) opens, driven by the same
                          viseme / audio level the old avatar used
  * living head        -- breathing, tiny sway, nods while speaking, looks away
                          while thinking, leans in while listening, glance()
  * eye glow           -- soft light on the irises in the HUD accent colour

No GPU, no extra packages. If the image is missing the constructor raises, and
ui.py falls back to the old holographic head.

The public surface matches HoloAvatar exactly:
    SPAN, glance(dx, dy, hold), step(dt, amp, **state), paint(p, cx, cy, r, main, acc, bg)
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath,
    QPen, QPolygonF, QRadialGradient,
)

_ASSET = Path(__file__).resolve().parent / "jarvis_face.png"

# ---- geometry, in pixels of jarvis_face.png (540 x 720) --------------------
# A / B are the two eye corners, `ut` / `ub` how far the open eye rises above /
# falls below the line between them, `iris` the iris centre, `cheek` a patch of
# skin under the eye used to colour the eyelid.
_EYES = (
    dict(A=(162.0, 377.5), B=(232.0, 378.0), ut=18.0, ub=6.0,
         iris=(194.0, 373.0), cheek=(180, 398, 212, 410)),
    dict(A=(318.0, 373.0), B=(407.0, 364.0), ut=19.0, ub=7.5,
         iris=(358.0, 362.5), cheek=(342, 392, 374, 404)),
)
_SEAM_X, _SEAM_Y = 280.0, 538.0          # where the lips meet
_PATCH = (170, 490, 220, 160)            # x, y, w, h of the mouth work area
_PIVOT_Y = 0.80                          # head tilts around the base of the neck
_SLICES = 28                             # eyelid is built from this many slices


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def _smooth(t: float) -> float:
    t = _clamp(t)
    return t * t * (3.0 - 2.0 * t)


def _quad(p0, c, p1, t):
    u = 1.0 - t
    return (u * u * p0[0] + 2 * u * t * c[0] + t * t * p1[0],
            u * u * p0[1] + 2 * u * t * c[1] + t * t * p1[1])


class PortraitAvatar:
    # Height of the picture in units of the head radius the HUD hands us.
    SPAN = 2.0

    def __init__(self):
        img = QImage(str(_ASSET))
        if img.isNull():
            raise FileNotFoundError(f"portrait image not found: {_ASSET}")
        self._img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self._w = float(self._img.width())
        self._h = float(self._img.height())

        # animation state
        self._t = 0.0
        self._open = 0.0            # mouth, 0..1
        self._wide = 0.0
        self._lid = 0.0             # 0 open .. 1 closed (what is drawn)
        self._sleep_lid = 0.0
        self._blink_t = -1.0        # <0: not blinking
        self._next_blink = random.uniform(1.5, 3.5)
        self._double = False
        self._gx = self._gy = 0.0   # smoothed gaze
        self._glance = None         # (dx, dy, seconds_left)
        self._lean = 0.0
        self._amp = 0.0
        self._speaking = False
        self._muted = False
        self._state = "IDLE"
        self._glow = 0.0

        self._patch = QImage(_PATCH[2], _PATCH[3],
                             QImage.Format.Format_ARGB32_Premultiplied)
        self._lid_cols = [self._prepare_lid(e) for e in _EYES]

    # ------------------------------------------------------------------ setup
    @staticmethod
    def _eye_frame(e):
        ax, ay = e["A"]
        bx, by = e["B"]
        dx, dy = bx - ax, by - ay
        ln = math.hypot(dx, dy) or 1.0
        dx, dy = dx / ln, dy / ln
        nx, ny = dy, -dx
        if ny > 0:                      # make the normal point UP the screen
            nx, ny = -nx, -ny
        return (ax, ay), (bx, by), ((ax + bx) / 2.0, (ay + by) / 2.0), (nx, ny), (dx, dy)

    def _px(self, x: float, y: float) -> tuple[float, float, float]:
        x = int(_clamp(x, 0, self._w - 1))
        y = int(_clamp(y, 0, self._h - 1))
        c = self._img.pixelColor(x, y)
        return c.red(), c.green(), c.blue()

    def _prepare_lid(self, e):
        """Colour of the eyelid, one value per slice, sampled from the picture.

        Skin just above the lash line (brightest few samples, to skip the brow
        and the lash shadow) blended with the cheek under the eye -- so the lid
        carries the same light direction as the face around it.
        """
        A, B, M, n, d = self._eye_frame(e)
        ext = 0.03
        a0 = (A[0] - (B[0] - A[0]) * ext, A[1] - (B[1] - A[1]) * ext)
        b0 = (B[0] + (B[0] - A[0]) * ext, B[1] + (B[1] - A[1]) * ext)
        ctrl = (M[0] + n[0] * 2 * e["ut"], M[1] + n[1] * 2 * e["ut"])

        x0, y0, x1, y1 = e["cheek"]
        cs = [self._px(x, y) for x in range(x0, x1, 3) for y in range(y0, y1, 3)]
        cheek = [sum(c[i] for c in cs) / len(cs) for i in range(3)]

        cols = []
        for i in range(_SLICES + 1):
            p = _quad(a0, ctrl, b0, i / _SLICES)
            smp = [self._px(p[0] + n[0] * k, p[1] + n[1] * k) for k in range(3, 12)]
            smp.sort(key=sum)
            top3 = smp[-3:]
            sk = [sum(c[j] for c in top3) / 3.0 for j in range(3)]
            cols.append([sk[j] * 0.35 + cheek[j] * 0.65 for j in range(3)])
        # light smoothing along the lid so single noisy pixels do not show
        sm = []
        for i in range(len(cols)):
            lo, hi = max(0, i - 2), min(len(cols), i + 3)
            sm.append([sum(cols[k][j] for k in range(lo, hi)) / (hi - lo) for j in range(3)])
        return sm

    # ------------------------------------------------------------- public API
    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Look somewhere for a moment (dx, dy roughly -1..1)."""
        self._glance = (_clamp(dx, -1, 1), _clamp(dy, -1, 1), max(0.2, float(hold)))

    def step(self, dt: float, amp: float, speaking: bool = False, muted: bool = False,
             state: str = "", v_open=None, v_wide: float = 0.0, v_level=None,
             v_seq=None, v_hop: float = 0.02, **_ignored) -> None:
        dt = _clamp(dt, 0.0, 0.1)
        self._t += dt
        self._speaking = bool(speaking)
        self._muted = bool(muted)
        self._state = (state or "").upper()
        self._amp = amp = _clamp(amp)

        # ---- mouth ---------------------------------------------------------
        if v_open is not None:
            target = _clamp(float(v_open))
            self._wide += (_clamp(float(v_wide or 0.0), -1, 1) - self._wide) * min(1.0, dt * 20)
        elif speaking:
            target = _clamp(amp * 2.2)
            self._wide *= 0.9
        else:
            target = 0.0
            self._wide *= 0.9
        rate = 26.0 if target > self._open else 16.0
        self._open += (target - self._open) * min(1.0, dt * rate)

        # ---- gaze / lean ---------------------------------------------------
        tx = ty = 0.0
        lean = 0.0
        if self._state in ("THINKING", "PROCESSING"):
            tx, ty = 0.7, -0.5          # looks away while it works
        elif self._state == "LISTENING":
            lean = 1.0                  # leans in, meets your eyes
        if self._glance is not None:
            gx, gy, left = self._glance
            tx, ty = gx, gy
            left -= dt
            self._glance = (gx, gy, left) if left > 0 else None
        k = min(1.0, dt * 3.5)
        self._gx += (tx - self._gx) * k
        self._gy += (ty - self._gy) * k
        self._lean += (lean - self._lean) * min(1.0, dt * 2.5)

        # ---- eyes: sleep + blinking ---------------------------------------
        asleep = self._state == "SLEEPING"
        self._sleep_lid += ((0.88 if asleep else 0.0) - self._sleep_lid) * min(1.0, dt * 4.0)

        blink = 0.0
        if self._blink_t >= 0.0:
            self._blink_t += dt
            close_d, open_d = 0.06, 0.11
            if self._blink_t < close_d:
                blink = _smooth(self._blink_t / close_d)
            elif self._blink_t < close_d + open_d:
                blink = 1.0 - _smooth((self._blink_t - close_d) / open_d)
            else:
                self._blink_t = -1.0
                if not self._double and random.random() < 0.15:
                    self._double = True
                    self._blink_t = 0.0
                else:
                    self._double = False
                    self._next_blink = random.uniform(2.2, 5.5)
        elif not asleep:
            self._next_blink -= dt
            if self._next_blink <= 0.0:
                self._blink_t = 0.0
        self._lid = max(blink, self._sleep_lid)

        # eye glow follows speech
        self._glow += ((0.35 + 0.65 * amp if speaking else 0.30) - self._glow) * min(1.0, dt * 6)

    # ------------------------------------------------------------------ paint
    def paint(self, p: QPainter, cx: float, cy: float, r: float,
              main: QColor, acc: QColor, bg: QColor) -> None:
        hs = self.SPAN * r
        s = hs / self._h
        top = cy - r
        ws = self._w * s

        p.save()
        p.setRenderHints(QPainter.RenderHint.Antialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)

        # soft halo behind the portrait, tinted by the current state colour
        halo = QRadialGradient(QPointF(cx, top + hs * 0.42), hs * 0.62)
        c0 = QColor(acc)
        c0.setAlpha(38)
        c1 = QColor(acc)
        c1.setAlpha(0)
        halo.setColorAt(0.0, c0)
        halo.setColorAt(1.0, c1)
        p.fillRect(QRectF(cx - ws, top, ws * 2, hs), QBrush(halo))

        # head motion (all in picture pixels, converted by the final scale)
        t = self._t
        asleep = self._state == "SLEEPING"
        breath = math.sin(t * (0.75 if asleep else 1.25))
        ox = (math.sin(t * 0.43) * 3.0 + math.sin(t * 0.91) * 1.2) + self._gx * 12.0
        oy = breath * 2.0 + self._gy * 8.0 - self._lean * 4.0
        if self._speaking:
            oy += math.sin(t * 6.5) * self._amp * 2.2
        ang = math.sin(t * 0.31) * 0.5 + self._gx * 1.2 + (1.0 if self._gy < -0.2 else 0.0)
        zoom = 1.0 + breath * 0.004 + self._lean * 0.012

        pivot = self._h * _PIVOT_Y
        p.translate(cx + ox * s, top + pivot * s + oy * s)
        p.rotate(ang)
        p.scale(zoom, zoom)
        p.translate(-self._w / 2.0 * s, -pivot * s)
        p.scale(s, s)

        p.setOpacity(0.55 if self._muted else (0.85 if asleep else 1.0))
        p.drawImage(QPointF(0.0, 0.0), self._img)

        self._paint_eye_glow(p, acc)
        self._paint_mouth(p)
        if self._lid > 0.02:
            for i, e in enumerate(_EYES):
                self._paint_lid(p, e, self._lid_cols[i], self._lid)
        p.restore()

    # ---- eyes ---------------------------------------------------------------
    def _paint_eye_glow(self, p: QPainter, acc: QColor) -> None:
        open_amt = 1.0 - _clamp(self._lid * 1.4)
        if open_amt <= 0.02:
            return
        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
        for e in _EYES:
            ix, iy = e["iris"]
            g = QRadialGradient(QPointF(ix, iy), 30.0)
            a = QColor(acc)
            a.setAlpha(int(110 * self._glow * open_amt))
            z = QColor(acc)
            z.setAlpha(0)
            g.setColorAt(0.0, a)
            g.setColorAt(1.0, z)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(ix, iy), 30.0, 30.0)
        p.restore()

    def _paint_lid(self, p: QPainter, e, cols, c: float) -> None:
        A, B, M, n, d = self._eye_frame(e)
        he = e["ut"] * (1.0 - c) + (-e["ub"] * 0.8) * c      # lid edge height
        ht = e["ut"] + 6.5                                   # covers the lash band
        ext = 0.03
        a0 = (A[0] - (B[0] - A[0]) * ext, A[1] - (B[1] - A[1]) * ext)
        b0 = (B[0] + (B[0] - A[0]) * ext, B[1] + (B[1] - A[1]) * ext)
        ctop = (M[0] + n[0] * 2 * ht, M[1] + n[1] * 2 * ht)
        cedge = (M[0] + n[0] * 2 * he, M[1] + n[1] * 2 * he)
        top = [_quad(a0, ctop, b0, i / _SLICES) for i in range(_SLICES + 1)]
        edge = [_quad(a0, cedge, b0, i / _SLICES) for i in range(_SLICES + 1)]
        fade = _clamp(c * 4.0)

        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(_SLICES):
            r, g, b = cols[i]
            # slices overlap by a pixel so no hairline seams show between them
            poly = QPolygonF([
                QPointF(*top[i]),
                QPointF(top[i + 1][0] + d[0] * 0.9, top[i + 1][1] + d[1] * 0.9),
                QPointF(edge[i + 1][0] + d[0] * 0.9, edge[i + 1][1] + d[1] * 0.9),
                QPointF(*edge[i]),
            ])
            grad = QLinearGradient(QPointF(*top[i]), QPointF(*edge[i]))
            grad.setColorAt(0.00, QColor(int(r), int(g), int(b), int(255 * fade * 0.85)))
            grad.setColorAt(0.25, QColor(int(r), int(g), int(b), int(255 * fade)))
            grad.setColorAt(1.00, QColor(int(r * 0.93), int(g * 0.93), int(b * 0.93), int(255 * fade)))
            p.setBrush(QBrush(grad))
            p.drawPolygon(poly)

        # the lash line along the closing edge
        path = QPainterPath()
        path.moveTo(QPointF(*A))
        path.quadTo(QPointF(*cedge), QPointF(*B))
        pen = QPen(QColor(26, 16, 18, int(235 * fade)), 1.6 + 1.2 * c)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.restore()

    # ---- mouth --------------------------------------------------------------
    def _paint_mouth(self, p: QPainter) -> None:
        dy = _clamp(self._open) * 15.0
        if dy < 0.6:
            return
        px0, py0, pw, ph = _PATCH
        seam = _SEAM_Y
        img = self._img
        patch = self._patch
        patch.fill(Qt.GlobalColor.transparent)

        q = QPainter(patch)
        q.setRenderHints(QPainter.RenderHint.Antialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        # untouched upper part (upper lip and above)
        q.drawImage(QRectF(0, 0, pw, seam - py0), img, QRectF(px0, py0, pw, seam - py0))
        # the gap the lower jaw leaves is first filled with the stretched seam row
        q.drawImage(QRectF(0, seam - py0 - 0.5, pw, dy + 1.5), img,
                    QRectF(px0, seam - 1, pw, 1))

        # lower lip + chin slide down; the move fades out toward the neck
        def w(y: float) -> float:
            if y <= 588.0:
                return 1.0
            return 1.0 - _smooth((y - 588.0) / 57.0)

        y = seam
        while y < py0 + ph:
            y0 = y + dy * w(y)
            y1 = y + 2 + dy * w(y + 2)
            q.drawImage(QRectF(0, y0 - py0, pw, (y1 - y0) + 0.4), img,
                        QRectF(px0, y, pw, 2))
            y += 2

        # mouth interior: dark cavity, teeth along the top, tongue below
        wide = _clamp(self._wide, 0.0, 1.0)
        rx = 44.0 * (0.9 + 0.25 * wide)
        cxl = _SEAM_X - px0
        cyl = seam - py0 + dy / 2.0
        ry = dy / 2.0 + 0.8
        cav = QPainterPath()
        cav.addEllipse(QPointF(cxl, cyl), rx, ry)
        q.save()
        q.setClipPath(cav)
        q.fillRect(QRectF(cxl - rx, cyl - ry, rx * 2, ry * 2), QColor(58, 20, 26))
        th = min(dy * 0.38, 5.0)
        q.fillRect(QRectF(cxl - rx * 0.7, seam - py0, rx * 1.4, th), QColor(214, 206, 198))
        tongue = QPainterPath()
        tongue.addEllipse(QPointF(cxl, seam - py0 + dy * 0.82), rx * 0.55, dy * 0.22 + 0.5)
        q.fillPath(tongue, QColor(122, 42, 51))
        q.restore()

        # feather the whole patch into the face with an elliptical mask
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        q.save()
        q.translate(_SEAM_X - px0, 575.0 - py0)
        q.scale(1.0, 75.0 / 105.0)
        mask = QRadialGradient(QPointF(0, 0), 105.0)
        mask.setColorAt(0.0, QColor(0, 0, 0, 255))
        mask.setColorAt(0.6, QColor(0, 0, 0, 255))
        mask.setColorAt(1.0, QColor(0, 0, 0, 0))
        q.fillRect(QRectF(-400, -400, 800, 800), QBrush(mask))
        q.restore()
        q.end()

        p.drawImage(QPointF(px0, py0), patch)