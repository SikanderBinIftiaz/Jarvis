"""
Orb avatar -- a glowing energy orb instead of a face (drop-in for HoloAvatar).

  * standby / sleeping  -> calm BLUE ring, slow rotation ("system locked")
  * listening           -> brighter blue-cyan, ripples gather inward
  * thinking            -> gold, faster spin
  * speaking            -> fiery ORANGE core, the orb pulses with the voice and
                           ripples travel outward

Pure QPainter, no images, no extra packages. Same public surface as HoloAvatar:
    SPAN, glance(dx, dy, hold), step(dt, amp, **state), paint(p, cx, cy, r, main, acc, bg)
"""
from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QRadialGradient

# (body colour, bright core colour) per mood
_BLUE = ((40, 150, 255), (170, 225, 255))
_CYAN = ((60, 200, 255), (200, 245, 255))
_GOLD = ((255, 175, 45), (255, 235, 170))
_ORANGE = ((255, 112, 18), (255, 222, 150))


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def _lerp(a, b, t):
    return a + (b - a) * t


class OrbAvatar:
    SPAN = 2.0

    def __init__(self):
        rnd = random.Random(7)
        # drifting particles: angle, radius (in orb radii), size, angular speed, twinkle phase
        self._parts = [
            (rnd.uniform(0, math.tau), rnd.uniform(0.25, 1.75), rnd.uniform(0.6, 2.0),
             rnd.uniform(-0.25, 0.25), rnd.uniform(0, math.tau))
            for _ in range(90)
        ]
        self._t = 0.0
        self._amp = 0.0
        self._spin = 0.0
        self._pulse = 0.0
        self._ripple = 0.0
        self._body = list(_BLUE[0])
        self._core = list(_BLUE[1])
        self._speaking = False
        self._muted = False
        self._state = "IDLE"
        self._gx = self._gy = 0.0
        self._glance = None
        self._energy = 0.0       # 0 standby .. 1 fully awake

    # ------------------------------------------------------------- public API
    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        self._glance = (_clamp(dx, -1, 1), _clamp(dy, -1, 1), max(0.2, float(hold)))

    def step(self, dt: float, amp: float, speaking: bool = False, muted: bool = False,
             state: str = "", v_open=None, v_wide: float = 0.0, v_level=None,
             v_seq=None, v_hop: float = 0.02, **_ignored) -> None:
        dt = _clamp(dt, 0.0, 0.1)
        self._t += dt
        self._speaking = bool(speaking)
        self._muted = bool(muted)
        self._state = (state or "").upper()
        level = _clamp(float(v_level) if v_level is not None else amp)
        target_amp = _clamp(max(amp, level) * 1.6) if speaking else 0.0
        rate = 18.0 if target_amp > self._amp else 7.0
        self._amp += (target_amp - self._amp) * min(1.0, dt * rate)

        st = self._state
        if speaking or st == "SPEAKING":
            tgt, energy, spin = _ORANGE, 1.0, 0.9
        elif st in ("THINKING", "PROCESSING"):
            tgt, energy, spin = _GOLD, 0.8, 1.6
        elif st == "LISTENING":
            tgt, energy, spin = _CYAN, 0.55, 0.5
        else:
            tgt, energy, spin = _BLUE, 0.3, 0.25      # standby / sleeping: "locked"
        k = min(1.0, dt * 3.0)
        for i in range(3):
            self._body[i] += (tgt[0][i] - self._body[i]) * k
            self._core[i] += (tgt[1][i] - self._core[i]) * k
        self._energy += (energy - self._energy) * min(1.0, dt * 2.5)
        self._spin += dt * spin
        self._pulse += (self._amp - self._pulse) * min(1.0, dt * 14.0)
        self._ripple = (self._ripple + dt * (0.55 + 0.9 * self._pulse)) % 1.0

        tx = ty = 0.0
        if self._glance is not None:
            gx, gy, left = self._glance
            tx, ty = gx, gy
            left -= dt
            self._glance = (gx, gy, left) if left > 0 else None
        k = min(1.0, dt * 3.5)
        self._gx += (tx - self._gx) * k
        self._gy += (ty - self._gy) * k

    # ------------------------------------------------------------------ paint
    @staticmethod
    def _c(rgb, a: float) -> QColor:
        return QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]), int(_clamp(a) * 255))

    def paint(self, p: QPainter, cx: float, cy: float, r: float,
              main: QColor, acc: QColor, bg: QColor) -> None:
        mid_y = cy - r + self.SPAN * r / 2.0
        R = self.SPAN * r / 2.0 * 0.70
        ox = cx + self._gx * R * 0.05
        oy = mid_y + self._gy * R * 0.04
        t = self._t
        e = self._energy
        body, core = self._body, self._core
        dim = 0.55 if self._muted else 1.0
        asleep = self._state == "SLEEPING"
        if asleep:
            dim *= 0.8
        breathe = 1.0 + 0.012 * math.sin(t * 1.3) + 0.09 * self._pulse
        Rb = R * breathe

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setOpacity(dim)

        # 1. wide soft glow
        g = QRadialGradient(QPointF(ox, oy), Rb * 1.9)
        g.setColorAt(0.0, self._c(body, 0.30 + 0.20 * e))
        g.setColorAt(0.45, self._c(body, 0.10 + 0.08 * e))
        g.setColorAt(1.0, self._c(body, 0.0))
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(ox, oy), Rb * 1.9, Rb * 1.9)

        # 2. drifting particles
        for ang, rad, size, spd, ph in self._parts:
            a = ang + t * spd * (0.5 + e)
            rr = Rb * rad
            px, py = ox + math.cos(a) * rr, oy + math.sin(a) * rr * 0.92
            tw = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(t * 1.7 + ph))
            p.setBrush(self._c(core if rad < 1.1 else body, 0.55 * tw))
            p.drawEllipse(QPointF(px, py), size, size)

        # 3. ripples (outward when speaking, gathering inward when listening)
        for i in range(2):
            ph = (self._ripple + i * 0.5) % 1.0
            if self._state == "LISTENING" and not self._speaking:
                ph = 1.0 - ph
            rr = Rb * (0.9 + 0.75 * ph)
            fade = (1.0 - ph) * (0.15 + 0.35 * e)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(self._c(body, fade), 1.6))
            p.drawEllipse(QPointF(ox, oy), rr, rr)

        # 4. sphere body
        p.setPen(Qt.PenStyle.NoPen)
        sg = QRadialGradient(QPointF(ox, oy), Rb * 0.9)
        sg.setColorAt(0.0, self._c(core, 0.95))
        sg.setColorAt(0.18, self._c(core, 0.55 + 0.25 * e))
        sg.setColorAt(0.55, self._c(body, 0.35 + 0.25 * e))
        sg.setColorAt(0.9, self._c(body, 0.18))
        sg.setColorAt(1.0, self._c(body, 0.0))
        p.setBrush(QBrush(sg))
        p.drawEllipse(QPointF(ox, oy), Rb * 0.9, Rb * 0.9)

        # 5. fiery inner arcs (rotating bands of light)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k, (rad, wdt, span, spd) in enumerate(
                ((0.78, 3.0, 70, 1.0), (0.66, 2.2, 110, -1.4), (0.54, 1.6, 90, 1.9), (0.42, 1.2, 60, -2.3))):
            rr = Rb * rad
            pen = QPen(self._c(core if k % 2 else body, 0.55 + 0.30 * e), wdt)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            base = (self._spin * spd * 57.3 + k * 90.0) % 360.0
            rect_r = rr
            p.drawArc(QRectF(ox - rect_r, oy - rect_r, rect_r * 2, rect_r * 2),
                      int(base * 16), int(span * 16))
            p.drawArc(QRectF(ox - rect_r, oy - rect_r, rect_r * 2, rect_r * 2),
                      int((base + 180.0) * 16), int(span * 0.6 * 16))

        # 6. X core: four spokes that spin slowly
        spokes = 4
        for i in range(spokes):
            a = self._spin * 0.6 + math.pi / 4 + i * math.pi / 2
            x1, y1 = ox + math.cos(a) * Rb * 0.10, oy + math.sin(a) * Rb * 0.10
            x2, y2 = ox + math.cos(a) * Rb * 0.80, oy + math.sin(a) * Rb * 0.80
            p.setPen(QPen(self._c(core, 0.55 + 0.35 * e), 2.4 + 2.0 * self._pulse))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # 7. rim: bright ring, dashed rotating ring, thin outer ring
        p.setPen(QPen(self._c(core, 0.80), 2.6))
        p.drawEllipse(QPointF(ox, oy), Rb * 0.9, Rb * 0.9)
        dash = QPen(self._c(body, 0.85), 3.0)
        dash.setDashPattern([1.2, 3.2])
        dash.setDashOffset(-self._spin * 14.0)
        p.setPen(dash)
        p.drawEllipse(QPointF(ox, oy), Rb * 1.0, Rb * 1.0)
        p.setPen(QPen(self._c(body, 0.45), 1.0))
        p.drawEllipse(QPointF(ox, oy), Rb * 1.12, Rb * 1.12)

        # 8. hot centre
        p.setPen(Qt.PenStyle.NoPen)
        cg = QRadialGradient(QPointF(ox, oy), Rb * (0.20 + 0.10 * self._pulse))
        cg.setColorAt(0.0, QColor(255, 255, 255, 255))
        cg.setColorAt(0.35, self._c(core, 0.95))
        cg.setColorAt(1.0, self._c(core, 0.0))
        p.setBrush(QBrush(cg))
        p.drawEllipse(QPointF(ox, oy), Rb * 0.30, Rb * 0.30)
        p.restore()