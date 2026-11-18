"""Retro-neon vehicles: rebuilds build/assets/car.glb, heli.glb and airplane.glb.

Run headless:
  blender -b --factory-startup -P tools/retro/vehicles.py -- [--only car,heli,airplane] [--base HEAD]
      [--out build/assets] [--preview DIR] [--fit build/assets/boxman.glb]
--preview renders Workbench PNGs (front 3/4, side, rear 3/4, top, moving parts deflected); --fit adds the
seated hero at every seat to those previews.

Every vehicle starts from the ORIGINAL asset in git (`git show <base>:build/assets/<name>.glb`). All nodes
(seats, entrances, doors, wheels, steering wheel, camera, collision, rotors, flaps and their *_parent empties)
keep their names, parents, transforms and extras; only the visible mesh data is swapped. Moving parts are
built in their own node space, so wheels, doors, rotors and flaps still turn about the same pivots.

Seated astronaut: the helmet top sits ~0.78 above a seat node, so every closed shell over a seat clears
seat_y + 0.82, or the cabin is left open (car = open-top, plane = open cockpit with a windscreen).

Coordinates used below: x = vehicle left, f = forward, u = up  ->  Blender (x, -f, u).
"""
import bpy, bmesh, math, os, sys, subprocess, tempfile
from mathutils import Vector, Matrix, Quaternion

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C  # noqa: E402

REPO = C.REPO
ASSETS = ('car', 'heli', 'airplane')


# ---------------------------------------------------------------- args
def parse_args():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    opts = {'only': list(ASSETS), 'base': 'HEAD', 'out': os.path.join(REPO, 'build', 'assets'),
            'preview': None, 'fit': None}
    i = 0
    while i < len(argv):
        k = argv[i].lstrip('-')
        v = argv[i + 1]
        opts[k] = v.split(',') if k == 'only' else v
        i += 2
    return opts


# ---------------------------------------------------------------- geometry helpers
def P(x, f, u):
    return Vector((x, -f, u))


def _finish(name, bm, color=None, kind='retro', face_colors=None, smooth=None):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    if face_colors is None:
        C.paint(o, color, kind)
    else:
        paint_faces(o, face_colors, kind)
    for p in me.polygons:
        p.use_smooth = bool(smooth(p)) if smooth else False
    return o


def paint_faces(o, colors, kind='retro'):
    me = o.data
    me.materials.clear()
    me.materials.append(C.material(kind))
    if not me.uv_layers:
        me.uv_layers.new(name='UVMap')
    uv = me.uv_layers.active.data
    assert len(colors) == len(me.polygons), (o.name, len(colors), len(me.polygons))
    for p, col in zip(me.polygons, colors):
        u, v = C.swatch_uv(col)
        for li in p.loop_indices:
            uv[li].uv = (u, v)


def _dedupe(poly, eps=1e-6):
    out = []
    for p in poly:
        if not out or abs(out[-1][0] - p[0]) > eps or abs(out[-1][1] - p[1]) > eps:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) < eps and abs(out[0][1] - out[-1][1]) < eps:
        out.pop()
    return out


def solid_x(name, prof, x0, x1, color='white', kind='retro', edge_colors=None, cap_colors=None):
    """Extrude a side profile [(f, u)] across x from x0 to x1 (floats or functions of u).
    edge_colors(i) paints the face on profile edge i -> i+1; cap_colors = (colour at x0, colour at x1)."""
    prof = _dedupe(prof)
    fa = x0 if callable(x0) else (lambda u, v=x0: v)
    fb = x1 if callable(x1) else (lambda u, v=x1: v)
    bm = bmesh.new()
    A = [bm.verts.new((fa(u), -f, u)) for f, u in prof]
    B = [bm.verts.new((fb(u), -f, u)) for f, u in prof]
    cols = []
    bm.faces.new(A); cols.append(cap_colors[0] if cap_colors else color)
    bm.faces.new(list(reversed(B))); cols.append(cap_colors[1] if cap_colors else color)
    n = len(prof)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((A[i], A[j], B[j], B[i]))
        cols.append(edge_colors(i) if edge_colors else color)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _finish(name, bm, kind=kind, face_colors=cols)


def slab_u(name, poly_xf, u0, u1, color='white', kind='retro'):
    """Extrude a plan-view polygon [(x, f)] vertically from u0 to u1."""
    poly_xf = _dedupe(poly_xf)
    bm = bmesh.new()
    A = [bm.verts.new((x, -f, u0)) for x, f in poly_xf]
    B = [bm.verts.new((x, -f, u1)) for x, f in poly_xf]
    bm.faces.new(A)
    bm.faces.new(list(reversed(B)))
    n = len(poly_xf)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((A[i], A[j], B[j], B[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _finish(name, bm, color, kind)


OCT = [(1, -0.65), (1, 0.65), (0.65, 1), (-0.65, 1), (-1, 0.65), (-1, -0.65), (-0.65, -1), (0.65, -1)]


def loft(name, secs, color_fn, tmpl=OCT, caps=('white', 'white'), kind='retro'):
    """Low-poly hull through cross sections (f, u0, half_width, half_height); color_fn(segment, side)."""
    bm = bmesh.new()
    rings = [[bm.verts.new((w * sx, -f, u0 + h * su)) for sx, su in tmpl] for f, u0, w, h in secs]
    cols, m = [], len(tmpl)
    for i in range(len(rings) - 1):
        for k in range(m):
            bm.faces.new((rings[i][k], rings[i][(k + 1) % m], rings[i + 1][(k + 1) % m], rings[i + 1][k]))
            cols.append(color_fn(i, k))
    bm.faces.new(rings[0]); cols.append(caps[0])
    bm.faces.new(list(reversed(rings[-1]))); cols.append(caps[1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _finish(name, bm, kind=kind, face_colors=cols)


def bx(name, c, s, color, kind='retro'):
    """Box from centre (x, f, u) and size (sx, sf, su)."""
    return C.box(name, P(*c), (s[0], s[1], s[2]), color, kind)


def neon_bx(name, c, s, color, pad=0.05):
    return C.neon_strip(name, P(*c), (s[0], s[1], s[2]), color, glow=True, pad=pad)


def beam(name, p0, p1, t, color, kind='retro', t2=None):
    """Square bar between two (x, f, u) points."""
    a, b = P(*p0), P(*p1)
    d = b - a
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((t, t2 or t, d.length)), verts=bm.verts)
    rot = d.to_track_quat('Z', 'Y').to_matrix().to_4x4()
    bmesh.ops.transform(bm, matrix=Matrix.Translation((a + b) / 2) @ rot, verts=bm.verts)
    return _finish(name, bm, color, kind)


def cyl_x(name, xc, f, u, r, depth, color, segs=16, smooth=False, kind='retro', r2=None):
    """Cylinder (or cone) along x; side faces optionally smooth, caps always flat."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=r, radius2=r if r2 is None else r2,
                          depth=depth)
    bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'))
    bmesh.ops.translate(bm, verts=bm.verts, vec=P(xc, f, u))
    return _finish(name, bm, color, kind, smooth=(lambda p: smooth and abs(p.normal.x) < 0.9))


def glow_copy(o, grow):
    """Additive halo: a slightly inflated copy of a neon part."""
    g = o.copy()
    g.data = o.data.copy()
    bpy.context.scene.collection.objects.link(g)
    me = g.data
    cen = sum((v.co for v in me.vertices), Vector()) / len(me.vertices)
    for v in me.vertices:
        d = v.co - cen
        v.co = cen + Vector([d[i] + math.copysign(grow, d[i]) if abs(d[i]) > 1e-6 else 0 for i in range(3)])
    me.materials.clear()
    me.materials.append(C.material('glow'))
    return g


def clip_f(poly, f0, f1):
    """Clip a side profile [(f, u)] to f0 <= f <= f1."""
    def clip(pts, keep, fv):
        out = []
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            if keep(a):
                out.append(a)
            if keep(a) != keep(b):
                out.append((fv, a[1] + (b[1] - a[1]) * (fv - a[0]) / (b[0] - a[0])))
        return out
    return clip(clip(poly, lambda q: q[0] >= f0, f0), lambda q: q[0] <= f1, f1)


def arc(fc, uc, r, a0, a1, n):
    return [(fc + r * math.cos(a0 + (a1 - a0) * i / n), uc + r * math.sin(a0 + (a1 - a0) * i / n))
            for i in range(n + 1)]


def mirror_x(o):
    """Mirror a part across x = 0 in place (keeps outward normals)."""
    o.data.transform(Matrix.Scale(-1, 4, (1, 0, 0)))
    o.data.flip_normals()
    return o


# ---------------------------------------------------------------- node mesh swap
def bake(o):
    o.data.transform(o.matrix_basis)
    o.matrix_basis = Matrix.Identity(4)


def build_mesh(parts, name, target=None):
    """Join parts (built in vehicle space) into one mesh in `target`'s local space; returns the mesh."""
    for o in parts:
        bake(o)
    j = C.join(parts, '_tmp_' + name) if len(parts) > 1 else parts[0]
    me = j.data
    if target is not None:
        me.transform(target.matrix_world.inverted())
    me.name = name
    bpy.data.objects.remove(j)
    return me


def swap_mesh(target, me):
    old = target.data
    target.data = me
    if old.users == 0:
        bpy.data.meshes.remove(old)


def replace(target_name, parts, local=False):
    t = bpy.data.objects[target_name]
    me = build_mesh(parts, 'mesh_' + target_name, None if local else t)
    swap_mesh(t, me)
    return me


def wheel_mesh(tag, R, x_in, x_out, outer):
    """Tyre + chrome hub in wheel-node space, spinning about local x. outer = +1/-1 (side of the hub)."""
    xc, w = (x_in + x_out) / 2, x_out - x_in
    face = x_out if outer > 0 else x_in
    parts = [
        cyl_x(tag + 'tyre', xc, 0, 0, R, w * 0.86, 'tire', segs=16, smooth=True),
        cyl_x(tag + 'wall', xc, 0, 0, R * 0.9, w, 'tire', segs=16, smooth=True),
        cyl_x(tag + 'hub', face + outer * 0.001, 0, 0, R * 0.58, 0.01, 'hub', segs=10),
        cyl_x(tag + 'nut', face + outer * 0.010, 0, 0, R * 0.16, 0.012, 'black', segs=6),
    ]
    for k in range(3):  # six black spokes on the chrome hub
        sp = C.box(tag + f'sp{k}', (face + outer * 0.008, 0, 0), (0.006, R * 0.12, R * 1.04), 'black')
        sp.rotation_euler = (k * math.pi / 3, 0, 0)
        parts.append(sp)
    return build_mesh(parts, 'mesh_' + tag + 'wheel')


# ---------------------------------------------------------------- CAR
def build_car():
    O = bpy.data.objects
    WF, WR, WU, RA = 0.8644, -0.787, -0.1234, 0.275  # wheel centres (f, u), arch radius
    BOT, W = -0.10, 0.625
    PINK, DARK = 'pink', 'black'

    def belt(f):  # wedge beltline: low at the cowl, rising toward the tail
        return 0.33 + (0.56 - f) * 0.041

    def stripe_u(f):
        return 0.2175 + (0.56 - f) * 0.041

    def stripe(tag, sx, f0, f1):
        """Cyan neon stripe segment parallel to the beltline, with its halo."""
        b = beam(tag, (sx, f0, stripe_u(f0)), (sx, f1, stripe_u(f1)), 0.006, 'cyan', 'neon', t2=0.035)
        return [b, glow_copy(b, 0.02)]

    def arch_pts(fc, r=RA, ub=BOT, n=9):
        d = math.sqrt(r * r - (ub - WU) ** 2)
        a0 = math.atan2(ub - WU, d)
        return arc(fc, WU, r, a0, math.pi - a0, n)  # front end -> over the top -> back end

    # Lower body: hood, rear deck and a real cabin tub (floor u=0, raised rear platform over the arches).
    prof = [(1.27, BOT)] + arch_pts(WF) + arch_pts(WR) + [
        (-1.19, BOT), (-1.235, -0.05), (-1.235, 0.42), (-0.78, 0.385), (-0.78, 0.165), (-0.50, 0.165),
        (-0.50, 0.0), (0.56, 0.0), (0.56, belt(0.56)), (1.0, 0.225), (1.29, 0.11), (1.29, -0.03)]
    prof = _dedupe(prof)
    n_front = 1 + 10  # index ranges for arch edges (painted dark like wheel wells)
    cab_start = prof.index((-0.78, 0.385))
    cab_end = prof.index((0.56, belt(0.56)))

    def body_edge(i):
        if 1 <= i < 1 + 9 or 11 <= i < 11 + 9:
            return 'black'  # wheel wells
        if cab_start <= i < cab_end:
            return DARK     # cabin tub
        if i == len(prof) - 1 or i == 0 or i == n_front - 1 or i == n_front + 9:
            return 'pink_dark'  # underside
        return PINK

    body = [solid_x('car_body', prof, -W, W, PINK, edge_colors=body_edge)]

    # Cabin side walls where there is no door (front, B-pillar, rear quarter); inner faces darker pink.
    walls = [[(f0, lo), (f0, belt(f0)), (f1, belt(f1)), (f1, lo)]
             for f0, f1, lo in ((0.56, 0.487, 0.0), (-0.067, -0.171, 0.0), (-0.68, -0.78, 0.165))]
    for s in (1, -1):
        for k, wp in enumerate(walls):
            x0, x1 = (0.545, W) if s > 0 else (-W, -0.545)
            caps = ('pink_dark', PINK) if s > 0 else (PINK, 'pink_dark')
            body.append(solid_x(f'car_wall{k}{s}', wp, x0, x1, PINK, cap_colors=caps))

    # Seats, dash.
    for s in (1, -1):
        body.append(bx('car_seat', (s * 0.246, 0.07, 0.05), (0.40, 0.42, 0.10), 'dark'))
        body.append(bx('car_back', (s * 0.246, -0.25, 0.19), (0.40, 0.08, 0.36), 'dark'))
        body.append(bx('car_rseat', (s * 0.246, -0.40, 0.05), (0.40, 0.20, 0.10), 'dark'))
    body.append(bx('car_dash', (0, 0.60, 0.30), (1.09, 0.10, 0.10), 'black'))

    # Raked glass windscreen in a pink frame; it stands ahead of the driver's helmet (open top).
    ws = [(0.665, 0.32), (0.625, 0.32), (0.48, 0.67), (0.52, 0.67)]
    body.append(solid_x('car_screen', ws, -0.52, 0.52, 'glass'))
    for s in (1, -1):
        body.append(beam('car_apillar', (s * 0.545, 0.655, 0.31), (s * 0.545, 0.495, 0.695), 0.045, PINK))
    body.append(bx('car_header', (0, 0.497, 0.69), (1.135, 0.06, 0.04), PINK))

    # Pop-up headlight pods (black) with yellow neon lamps.
    for s in (1, -1):
        body.append(bx('car_pod', (s * 0.36, 1.0, 0.245), (0.24, 0.17, 0.11), 'black'))
        body += neon_bx('car_lamp', (s * 0.36, 1.089, 0.255), (0.19, 0.008, 0.045), 'yellow', pad=0.04)
    body.append(bx('car_grille', (0, 1.293, 0.02), (1.06, 0.012, 0.06), 'black'))

    # Tail: black panel with pink neon bars.
    body.append(bx('car_tailpanel', (0, -1.24, 0.30), (1.16, 0.012, 0.16), 'black'))
    for s in (1, -1):
        body += neon_bx('car_tail', (s * 0.33, -1.249, 0.30), (0.36, 0.008, 0.05), 'pink', pad=0.03)

    # Rear wing on two struts.
    for s in (1, -1):
        body.append(bx('car_strut', (s * 0.38, -1.08, 0.47), (0.04, 0.07, 0.18), 'black'))
        body.append(bx('car_endplate', (s * 0.588, -1.08, 0.56), (0.022, 0.27, 0.11), 'black'))
    body.append(bx('car_wing', (0, -1.08, 0.572), (1.16, 0.23, 0.035), 'black'))

    # Cyan neon side stripe on the bodywork between the doors.
    for s in (1, -1):
        for f0, f1 in ((0.92, 0.492), (-0.072, -0.166), (-0.688, -1.20)):
            body += stripe('car_stripe', s * (W + 0.003), f0, f1)

    # Violet underglow: flat additive plane under the floor, facing up.
    bm = bmesh.new()
    vs = [bm.verts.new(P(x, f, -0.112)) for x, f in ((0.70, 1.36), (-0.70, 1.36), (-0.70, -1.30), (0.70, -1.30))]
    face = bm.faces.new(vs)
    bm.normal_update()
    if face.normal.z < 0:
        face.normal_flip()
    body.append(_finish('car_underglow', bm, 'violet', 'glow'))

    replace('body', body)

    # Doors: pink side panels (old door outline), cyan stripe, black handle; hinge = node origin.
    def door_profile(name):
        d = O[name]
        hinge_f = -d.matrix_world.translation.y
        if hinge_f > 0:  # front door
            back = hinge_f - 0.554
            return [(hinge_f, belt(hinge_f) - 0.005), (hinge_f, -0.085), (back, -0.085), (back, belt(back) - 0.005)]
        back = hinge_f - 0.509  # rear door: notch for the rear wheel arch
        rr = RA + 0.005
        fa = WR + math.sqrt(rr * rr - (-0.085 - WU) ** 2)
        a0 = math.atan2(-0.085 - WU, fa - WR)
        a1 = math.acos((back - WR) / rr)
        return ([(hinge_f, belt(hinge_f) - 0.005), (hinge_f, -0.085)] + arc(WR, WU, rr, a0, a1, 6)
                + [(back, belt(back) - 0.005)])

    for name in ('door_1', 'door_2', 'door_3', 'door_4'):
        d = O[name]
        s = 1 if d.matrix_world.translation.x > 0 else -1
        prof = door_profile(name)
        f_front, f_back = prof[0][0], prof[-1][0]
        x0, x1 = (0.548, W) if s > 0 else (-W, -0.548)
        caps = ('pink_dark', PINK) if s > 0 else (PINK, 'pink_dark')
        parts = [solid_x(name + '_panel', prof, x0, x1, PINK, cap_colors=caps)]
        parts += stripe(name + '_stripe', s * (W + 0.003), f_front - 0.005, f_back + 0.005)
        parts.append(bx(name + '_handle', (s * (W + 0.006), f_back + 0.09, stripe_u(f_back) + 0.075),
                        (0.012, 0.075, 0.024), 'black'))
        replace(name, parts)

    # Steering wheel: black ring in its own node space (spins about local glTF z = Blender -y).
    ring = C.torus('sw_ring', (0, 0, 0), 0.085, 0.013, 'black', segs=12, ring_segs=5)
    ring.rotation_euler = (math.pi / 2, 0, 0)
    col = C.cylinder('sw_col', (0, -0.07, 0), 0.016, 0.14, 'black', segs=6, axis='Y')
    spoke = C.box('sw_spoke', (0, 0, 0), (0.17, 0.012, 0.022), 'black')
    replace('Steering wheel', [ring, col, spoke], local=True)

    # Wheels: tyres keep the old 0.235 radius and the old inner/outer faces.
    left = wheel_mesh('carL', 0.235, -0.03, 0.122, +1)
    right = wheel_mesh('carR', 0.235, -0.122, 0.03, -1)
    for name in ('wheel_fl', 'Cylinder.001'):
        swap_mesh(O[name], left)
    for name in ('wheel_fr', 'wheel'):
        swap_mesh(O[name], right)


# ---------------------------------------------------------------- HELI
# Hull ring: octagon whose flat sides are split into a lower panel, pink band, gap, cyan band, belt, window.
HELI_LV = [-0.68, -0.36, -0.22, -0.15, -0.01, 0.10, 0.68]  # side split heights (fraction of half height)
HELI_T = ([(1, v) for v in HELI_LV] + [(0.68, 1), (-0.68, 1)] + [(-1, v) for v in reversed(HELI_LV)]
          + [(-0.68, -1), (0.68, -1)])
HELI_BANDS = {1: 'pink', 3: 'cyan', 11: 'cyan', 13: 'pink'}


def build_heli():
    O = bpy.data.objects
    # (f, u0, half width, half height). Seats at u=-0.255 -> helmet top ~0.53; hull top here is 0.66.
    S = [(0.93, -0.13, 0.20, 0.20), (0.82, -0.07, 0.40, 0.42), (0.58, 0.0, 0.54, 0.60), (0.15, 0.02, 0.565, 0.64),
         (-0.35, 0.03, 0.55, 0.60), (-0.68, 0.17, 0.32, 0.32), (-0.82, 0.31, 0.15, 0.14),
         (-1.70, 0.465, 0.075, 0.075), (-1.80, 0.47, 0.05, 0.05)]

    def hull_col(i, k):
        if k in HELI_BANDS:
            return HELI_BANDS[k]
        if i <= 2 and k in (5, 6, 7, 8, 9):
            return 'glass'  # bubble canopy
        if i == 3 and k in (5, 9):
            return 'glass'  # side windows
        return 'white'

    body = [loft('heli_hull', S, hull_col, tmpl=HELI_T)]
    # Engine fairing + mast.
    body.append(loft('heli_engine', [(0.05, 0.60, 0.12, 0.06), (-0.06, 0.64, 0.25, 0.12), (-0.45, 0.64, 0.25, 0.12),
                                     (-0.64, 0.56, 0.12, 0.08)], lambda i, k: 'white'))
    rotor_hub = O['Empty.001'].matrix_world.translation
    body.append(C.cylinder('heli_mast', (0, rotor_hub.y, (0.70 + rotor_hub.z) / 2), 0.045, rotor_hub.z - 0.70 + 0.01,
                           'black', segs=8))
    # Door openings (seen only when a door swings out).
    for s in (1, -1):
        body.append(bx('heli_opening', (s * 0.5555, 0.135, -0.205), (0.021, 0.50, 0.44), 'black'))

    # Tail: pink neon fins, white stabiliser, tail-rotor shaft.
    for prof in ([(-1.50, 0.50), (-1.70, 0.93), (-1.83, 0.93), (-1.80, 0.50)],
                 [(-1.60, 0.44), (-1.75, 0.22), (-1.84, 0.22), (-1.80, 0.44)]):
        fin = solid_x('heli_fin', prof, -0.018, 0.018, 'pink', 'neon')
        body += [fin, glow_copy(fin, 0.035)]
    body.append(bx('heli_stab', (0, -1.33, 0.415), (0.42, 0.15, 0.03), 'white'))
    tail = O['Cube.002'].matrix_world.translation
    body.append(cyl_x('heli_tailshaft', tail.x / 2, -tail.y, tail.z, 0.025, tail.x, 'black', segs=6))

    # Cyan neon skids on black struts.
    for s in (1, -1):
        x = s * 0.42
        body += neon_bx('heli_skid', (x, 0.06, -0.645), (0.045, 1.12, 0.045), 'cyan', pad=0.04)
        for p0, p1 in (((x, 0.60, -0.645), (x, 0.74, -0.575)), ((x, -0.48, -0.645), (x, -0.56, -0.61))):
            tip = beam('heli_skidtip', p0, p1, 0.045, 'cyan', 'neon')
            body += [tip, glow_copy(tip, 0.02)]
        for f in (0.38, -0.28):
            body.append(beam('heli_strut', (s * 0.30, f, -0.55), (x, f, -0.63), 0.04, 'black'))
    replace('Cube', body)

    # Doors: thin white panels carrying the hull bands along the same lines; hinge = node origin.
    def hull_at(f):
        for a, b in zip(S, S[1:]):
            if b[0] <= f <= a[0]:
                t = (f - a[0]) / (b[0] - a[0])
                return tuple(a[i] + (b[i] - a[i]) * t for i in (1, 2, 3))
        raise ValueError(f)

    for name in ('door_L', 'door_R'):
        d = O[name]
        hp = d.matrix_world.translation
        s = 1 if hp.x > 0 else -1
        hf, back = -hp.y, -hp.y - 0.507
        x0, x1 = (0.568, 0.580) if s > 0 else (-0.580, -0.568)
        parts = [solid_x(name + '_panel', [(hf, 0.02), (hf, -0.43), (back + 0.05, -0.43), (back, -0.38),
                                           (back, 0.02)], x0, x1, 'white')]
        fs = [f for f in (hf, S[3][0], back) if back <= f <= hf]
        for (lo, hi), col in (((HELI_LV[1], HELI_LV[2]), 'pink'), ((HELI_LV[3], HELI_LV[4]), 'cyan')):
            top = [(f, hull_at(f)[0] + hull_at(f)[2] * hi) for f in fs]
            bot = [(f, hull_at(f)[0] + hull_at(f)[2] * lo) for f in reversed(fs)]
            bx0, bx1 = (0.580, 0.584) if s > 0 else (-0.584, -0.580)
            parts.append(solid_x(name + '_band', top + bot, bx0, bx1, col))
        parts.append(bx(name + '_handle', (s * 0.586, back + 0.08, -0.125), (0.008, 0.07, 0.02), 'black'))
        replace(name, parts)

    # Main rotor (node space: spins about local x, blades in the local y-z plane).
    mr = [C.box('mr_a', (-0.015, 0, 0), (0.025, 3.90, 0.13), 'black'),
          C.box('mr_b', (-0.015, 0, 0), (0.025, 0.13, 3.90), 'black'),
          C.cylinder('mr_hub', (-0.02, 0, 0), 0.10, 0.07, 'chrome', segs=8, axis='X')]
    replace('Cube.004', mr, local=True)
    # Tail rotor (node space, spins about local x).
    tr = [C.box('tr_a', (0.027, 0, 0), (0.018, 0.76, 0.075), 'yellow'),
          C.box('tr_b', (0.027, 0, 0), (0.018, 0.075, 0.76), 'yellow'),
          C.cylinder('tr_hub', (0.027, 0, 0), 0.045, 0.05, 'black', segs=8, axis='X')]
    replace('Cube.002', tr, local=True)


# ---------------------------------------------------------------- AIRPLANE
def build_airplane():
    O = bpy.data.objects

    def lerp_sec(a, b, f):
        t = (f - a[0]) / (b[0] - a[0])
        return (f,) + tuple(a[i] + (b[i] - a[i]) * t for i in (1, 2, 3))

    # Fuselage (f, u0, w, h); sunset rings behind the open cockpit.
    A = (-0.40, 0.33, 0.27, 0.26)
    Z = (-1.40, 0.46, 0.07, 0.08)
    bands = [(-0.72, -0.82, 'yellow'), (-0.855, -0.945, 'orange'), (-0.98, -1.05, 'pink'), (-1.085, -1.135, 'violet')]
    secs = [(0.835, 0.287, 0.16, 0.15), (0.70, 0.29, 0.26, 0.25), (0.45, 0.30, 0.30, 0.285),
            (0.05, 0.31, 0.31, 0.29), A, lerp_sec(A, Z, -0.645)]
    seg_col = ['white'] * 5
    for f0, f1, col in bands:
        secs.append(lerp_sec(A, Z, f0)); seg_col.append('white')
        secs.append(lerp_sec(A, Z, f1)); seg_col.append(col)
    secs += [Z, (-1.48, 0.47, 0.035, 0.04)]
    seg_col += ['white', 'white']

    def fus_col(i, k):
        # open cockpit: the top faces between the windscreen and the headrest are a dark well
        if i in (3, 4) and k in (1, 2, 3):
            return 'black'
        return seg_col[i]

    body = [loft('plane_fuselage', secs, fus_col, caps=('white', 'white'))]
    # Cockpit rim (pink coaming) around the dark well.
    for s in (1, -1):
        body.append(beam('plane_rim', (s * 0.215, 0.06, 0.60), (s * 0.215, -0.40, 0.59), 0.035, 'pink'))
        body.append(beam('plane_rim2', (s * 0.215, -0.40, 0.59), (s * 0.17, -0.645, 0.565), 0.035, 'pink'))
    # Headrest fairing behind the pilot, tapering into the fin.
    body.append(loft('plane_headrest', [(-0.645, 0.53, 0.19, 0.06), (-0.68, 0.56, 0.19, 0.14),
                                        (-0.95, 0.56, 0.08, 0.08), (-1.10, 0.52, 0.03, 0.03)], lambda i, k: 'white'))
    # Glass windscreen ahead of the open cockpit.
    body.append(solid_x('plane_screen', [(0.17, 0.57), (0.13, 0.57), (0.05, 0.80), (0.09, 0.80)], -0.22, 0.22,
                        'glass'))
    body.append(bx('plane_screenframe', (0, 0.07, 0.805), (0.46, 0.05, 0.025), 'pink'))

    # Wings with pink neon tips; the notch behind f=-0.105 is the aileron bay.
    wing = [(0.20, 0.27), (1.645, 0.215), (1.645, -0.107), (0.868, -0.121), (0.868, -0.24), (0.20, -0.24)]
    for s in (1, -1):
        w = slab_u('plane_wing', [(s * x, f) for x, f in wing], 0.236, 0.296, 'white')
        body.append(w)
        body += neon_bx('plane_tip', (s * 1.71, -0.008, 0.266), (0.13, 0.45, 0.06), 'pink', pad=0.03)
    # Tailplane (elevator bay behind f=-1.159) and the fixed fin.
    tp = [(0.04, -0.86), (0.62, -1.02), (0.62, -1.239), (0.254, -1.161), (0.254, -1.274), (0.04, -1.274)]
    for s in (1, -1):
        body.append(slab_u('plane_tailplane', [(s * x, f) for x, f in tp], 0.406, 0.448, 'white'))
    body.append(solid_x('plane_fin', [(-0.80, 0.55), (-1.17, 1.265), (-1.337, 1.265), (-1.209, 0.762),
                                      (-1.47, 0.69), (-1.47, 0.50), (-1.0, 0.50)], -0.024, 0.024, 'white'))
    # Landing gear legs.
    for s in (1, -1):
        body.append(bx('plane_fork', (s * 0.05, 0.492, -0.035), (0.018, 0.035, 0.24), 'black'))
        body.append(beam('plane_leg', (s * 0.10, -0.287, 0.07), (s * 0.188, -0.287, -0.152), 0.035, 'black'))
    body.append(bx('plane_forktop', (0, 0.492, 0.085), (0.12, 0.035, 0.03), 'black'))
    replace('body', body)

    # Control surfaces, built in their own node space (hinge on local y=0, chord along +y, span along x).
    def flap(tag, span, chord, thick, color, kind='retro', glow=0.0):
        # wedge section: thick at the hinge (local y=0), thin trailing edge at y=+chord (solid_x maps f -> -y)
        o = solid_x(tag, [(0, -thick), (0, thick), (-chord, 0)], -span, span, color, kind)
        return [o, glow_copy(o, glow)] if glow else [o]

    replace('aileron.L', flap('ail', 0.386, 0.118, 0.028, 'white'), local=True)
    replace('aileron.R', flap('ail', 0.386, 0.118, 0.028, 'white'), local=True)
    replace('elevator.L', flap('elev', 0.184, 0.113, 0.022, 'white'), local=True)
    replace('elevator.R', flap('elev', 0.184, 0.113, 0.022, 'white'), local=True)
    replace('rudder', flap('rud', 0.256, 0.128, 0.022, 'cyan', 'neon', glow=0.02), local=True)

    # Propeller on the rotor node (spins about local x; local -x points forward).
    prop = [C.cylinder('spinner', (-0.06, 0, 0), 0.004, 0.12, 'pink', segs=8, axis='X', radius2=0.075)]
    for sgn in (1, -1):
        b = C.box('blade', (-0.03, sgn * 0.235, 0), (0.018, 0.44, 0.08), 'black')
        b.rotation_euler = (0, sgn * 0.35, 0)  # blade pitch about its own long axis (local y)
        prop.append(b)
    replace('rotor', prop, local=True)

    # Wheels (old radius 0.113 and widths).
    nose = wheel_mesh('noseW', 0.113, -0.036, 0.036, +1)
    swap_mesh(O['wheel_fl'], nose)
    swap_mesh(O['wheel_fl.001'], wheel_mesh('planeL', 0.113, -0.006, 0.067, +1))
    swap_mesh(O['wheel_fl.002'], wheel_mesh('planeR', 0.113, -0.067, 0.006, -1))


BUILDERS = {'car': build_car, 'heli': build_heli, 'airplane': build_airplane}


# ---------------------------------------------------------------- previews
MOVES = {
    'car': [('door_1', 'Z', -1.0), ('door_3', 'Z', -0.8), ('door_2', 'Z', 1.0), ('Steering wheel', 'Y', 0.8)],
    'heli': [('door_L', 'Z', -1.0), ('Cube.004', 'X', 0.6), ('Cube.002', 'X', 0.6)],
    'airplane': [('aileron.L', 'X', 0.5), ('aileron.R', 'X', -0.5), ('elevator.L', 'X', 0.5),
                 ('elevator.R', 'X', 0.5), ('rudder', 'X', 0.5), ('rotor', 'X', 0.7)],
}


def posed_hero_obj(glb, anim, out):
    """Pose the hero GLB with the first keyframe of `anim`, the way three.js does (Blender's glTF importer
    misplaces the astronaut's bone-parented parts when posed), and write the rigid parts as an OBJ (glTF axes)."""
    import json, struct
    b = open(glb, 'rb').read()
    jl = struct.unpack('<I', b[12:16])[0]
    g = json.loads(b[20:20 + jl])
    bl = struct.unpack('<I', b[20 + jl:24 + jl])[0]
    blob = b[28 + jl:28 + jl + bl]
    ctype = {5126: ('f', 4), 5123: ('H', 2), 5125: ('I', 4), 5121: ('B', 1)}
    ncomp = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}

    def acc(i):
        a = g['accessors'][i]
        bv = g['bufferViews'][a['bufferView']]
        fmt, sz = ctype[a['componentType']]
        n = ncomp[a['type']]
        st, base = bv.get('byteStride', sz * n), bv.get('byteOffset', 0) + a.get('byteOffset', 0)
        return [struct.unpack_from('<' + fmt * n, blob, base + k * st) for k in range(a['count'])]

    an = next(a for a in g['animations'] if a['name'] == anim)
    key = {(c['target']['node'], c['target']['path']): acc(an['samplers'][c['sampler']]['output'])[0]
           for c in an['channels']}
    lines, faces, nv = [], [], [0]

    def walk(i, parent):
        n = g['nodes'][i]
        t, r, sc = (key.get((i, k), n.get(k, d)) for k, d in
                    (('translation', (0, 0, 0)), ('rotation', (0, 0, 0, 1)), ('scale', (1, 1, 1))))
        m = parent @ Matrix.Translation(t) @ Quaternion((r[3], r[0], r[1], r[2])).to_matrix().to_4x4() \
            @ Matrix.Diagonal((sc[0], sc[1], sc[2], 1))
        if 'mesh' in n and 'skin' not in n:
            for pr in g['meshes'][n['mesh']]['primitives']:
                base = nv[0]
                for v in acc(pr['attributes']['POSITION']):
                    w = m @ Vector(v)
                    lines.append('v %.5f %.5f %.5f' % tuple(w))
                    nv[0] += 1
                idx = [k[0] for k in acc(pr['indices'])]
                faces.extend('f %d %d %d' % (base + idx[k] + 1, base + idx[k + 1] + 1, base + idx[k + 2] + 1)
                             for k in range(0, len(idx), 3))
        for c in n.get('children', []):
            walk(c, m)

    for r in g['scenes'][g.get('scene', 0)]['nodes']:
        walk(r, Matrix.Identity(4))
    with open(out, 'w') as fh:
        fh.write('\n'.join(lines + faces) + '\n')
    return out


def place_astronauts(fit_obj):
    """Drop the seated hero (OBJ posed with the 'driving' keyframe, glTF axes, model-root space) at every
    seat to check that no part pokes through the bodywork."""
    added = []
    for seat in [o for o in bpy.data.objects if o.get('data') == 'seat']:
        before = set(bpy.data.objects)
        bpy.ops.wm.obj_import(filepath=fit_obj)
        for o in [o for o in bpy.data.objects if o not in before]:
            C.paint(o, 'suit')
            mw = seat.matrix_world
            # game: character = seat + 0.6 up (capsule), model container -0.57 -> model root at seat + 0.03 up
            o.matrix_world = (Matrix.Translation(mw.translation + Vector((0, 0, 0.03)))
                              @ mw.to_quaternion().to_matrix().to_4x4() @ o.matrix_world)
            added.append(o)
    return added


def render_previews(veh, outdir, fit_obj=None):
    os.makedirs(outdir, exist_ok=True)
    for o in bpy.data.objects:
        if o.get('data') == 'collision':
            o.hide_render = True
    vis = [o for o in bpy.data.objects if o.type == 'MESH' and not o.hide_render]
    out = {}
    views = {'front34': (1.0, -1.4, 0.8), 'side': (1.0, 0.0, 0.06), 'rear34': (-1.0, 1.3, 0.7),
             'top': (0.0, 0.47, 1.0)}
    for tag, d in views.items():
        p = os.path.join(outdir, f'{veh}_{tag}.png')
        C.render_preview(p, vis, size=(800, 600), cam_dir=d)
        out[tag] = p
    saved = {}
    for name, axis, ang in MOVES[veh]:
        o = bpy.data.objects[name]
        saved[name] = o.rotation_quaternion.copy()
        o.rotation_quaternion = o.rotation_quaternion @ Quaternion(Vector({'X': (1, 0, 0), 'Y': (0, 1, 0),
                                                                            'Z': (0, 0, 1)}[axis]), ang)
    bpy.context.view_layer.update()
    p = os.path.join(outdir, f'{veh}_moved.png')
    C.render_preview(p, vis, size=(800, 600), cam_dir=(1.0, -1.1, 0.75))
    out['moved'] = p
    for name, q in saved.items():
        bpy.data.objects[name].rotation_quaternion = q
    if fit_obj:
        added = place_astronauts(fit_obj)
        bpy.context.view_layer.update()
        for tag, d in (('fit_side', (1.0, 0.0, 0.06)), ('fit_front34', (1.0, -1.4, 0.8)),
                       ('fit_top', (0.0, 0.47, 1.0))):
            p = os.path.join(outdir, f'{veh}_{tag}.png')
            C.render_preview(p, vis, size=(800, 600), cam_dir=d)
            out[tag] = p
        for o in added:
            bpy.data.objects.remove(o)
    return out


# ---------------------------------------------------------------- main
def original(name, base, tmp):
    path = os.path.join(tmp, f'{name}_orig.glb')
    data = subprocess.run(['git', '-C', REPO, 'show', f'{base}:build/assets/{name}.glb'], check=True,
                          capture_output=True).stdout
    with open(path, 'wb') as fh:
        fh.write(data)
    return path


def main():
    opts = parse_args()
    tmp = tempfile.mkdtemp(prefix='retro_vehicles_')
    if opts['fit'] and opts['fit'].lower().endswith('.glb'):
        opts['fit'] = posed_hero_obj(opts['fit'], 'driving', os.path.join(tmp, 'hero_driving.obj'))
    for veh in opts['only']:
        C.reset()
        C.import_glb(original(veh, opts['base'], tmp))
        bpy.context.view_layer.update()
        BUILDERS[veh]()
        # drop data the export no longer references
        for me in list(bpy.data.meshes):
            if me.users == 0:
                bpy.data.meshes.remove(me)
        out = os.path.join(opts['out'], f'{veh}.glb')
        C.export_glb(out)
        print(f'[vehicles] wrote {out} ({os.path.getsize(out)} bytes)')
        if opts['preview']:
            for k, p in render_previews(veh, opts['preview'], opts['fit']).items():
                print(f'[vehicles] preview {k}: {p}')


if __name__ == '__main__':
    main()
