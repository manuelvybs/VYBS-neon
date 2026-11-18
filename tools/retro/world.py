"""Repaint the Sketchbook world for the retro neon night look.

Run: Blender -b --factory-startup -P tools/retro/world.py -- <original world.glb> <out.glb> [preview_dir]
  (original: git show HEAD:build/assets/world.glb > /tmp/world_orig.glb)

How it works, so every physics / spawn / path / scenario node survives byte for byte:
1. The original GLB JSON is kept as is. Only the image payloads change: each texture is gradient-mapped
   (luminance -> palette ramp), downscaled and re-encoded. s1..s6 share one albedo in the original, so each
   gets its own recoloured copy.
2. The original is imported into Blender only to read its geometry. Neon outlines, neon kerbs and palm trees
   are built there and exported alone to a temporary GLB.
3. That GLB is appended to the original JSON (new nodes, meshes, materials, buffers), plus one invisible
   box-collider node per palm trunk (extras data=physics, type=box, scale = half extents).
Blender coords: Z up. glTF coords: Y up, (x, y, z)_gltf = (x, z, -y)_blender.
"""
import bpy, bmesh, sys, os, math, json, struct, random, shutil, tempfile
import numpy as np
from collections import defaultdict
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import convex_hull_2d
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

argv = sys.argv[sys.argv.index('--') + 1:]
SRC, OUT = argv[0], argv[1]
PREVIEW = argv[2] if len(argv) > 2 else None
TMP = tempfile.mkdtemp(prefix='retro_world_')
PAL = dict(C.PALETTE)
SEED = 1987


def col(c):
    """Palette name or #hex -> sRGB floats."""
    return np.array(C.hex_rgb(PAL.get(c, c)), np.float32)


def mix(a, b, t):
    ca, cb = col(a), col(b)
    return '#' + ''.join(f'{int(round(255 * v)):02X}' for v in ca * (1 - t) + cb * t)


# ---------------------------------------------------------------- texture ramps
# material -> (stops [(source luminance, colour)], max size, format)
# Luminance is Rec.709 on the sRGB values; stops outside the range clamp.
RAMPS = {
    # court floors: light concrete -> dark purple ground, mottling kept subtle
    'concrete':       ([(0.57, '#150A27'), (0.65, 'asphalt'), (0.73, 'walk')], 512, 'JPEG'),
    # court walls: panel seams and the coping band survive as dark lines on purple
    'wall_segmented': ([(0.30, '#1A0A26'), (0.52, '#3E1A58'), (0.70, mix('purple_wall', 'magenta_wall', 0.4)),
                        (0.82, mix('purple_wall', 'magenta_wall', 0.65))], 512, 'JPEG'),
    # pillars, bowl, ramp sides, tunnel floors: rough concrete -> teal
    'wall_rough':     ([(0.12, '#05211F'), (0.20, '#0A4F49'), (0.30, 'teal'), (0.38, '#17A391')], 512, 'JPEG'),
    # the oval: near-black asphalt, white lines and hatching -> neon yellow
    'race_track':     ([(0.20, '#0A0514'), (0.42, '#1D0F33'), (0.58, '#24123E'), (0.76, 'yellow')], 512, 'JPEG'),
    # terrace (the "roof" of the whole platform): banded light grey -> roof purple with an indigo cast
    'roof':           ([(0.71, '#22123E'), (0.77, 'roof'), (0.82, mix('roof', 'indigo', 0.35))], 512, 'JPEG'),
    # tan paths, test ramps, mega ramp, loop ramp, hoops -> dark to violet
    'plaster':        ([(0.25, '#1C1040'), (0.36, '#3A2266')], 512, 'JPEG'),
    # figure-8 dirt: very dark with a magenta cast
    'dirt_road':      ([(0.44, '#120818'), (0.55, '#24102E'), (0.66, '#3E1646')], 512, 'JPEG'),
    # runway: near-black, edge lines and centre dashes -> cyan / white
    'runway':         ([(0.20, '#0A0514'), (0.40, '#1D0F33'), (0.55, '#24123E'), (0.78, 'cyan'), (0.95, 'white')],
                       512, 'JPEG'),
    # curved figure-8 side rails: grey bands -> pink bands
    'side_barrier':   ([(0.52, '#3A0A2A'), (0.62, 'pink_dark'), (0.74, 'pink')], 128, 'PNG'),
    # helipad atlas: metal plates indigo, yellow arc / H / hazard -> pink and white
    'helipad':        ([(0.15, '#0D0B33'), (0.35, 'deep'), (0.55, 'indigo'), (0.72, 'pink'), (0.90, 'white')],
                       512, 'JPEG'),
    # green sign with white arrow -> dark sign, yellow arrow
    'arrow_down':     ([(0.30, '#120818'), (0.40, 'asphalt'), (0.85, 'yellow')], 256, 'PNG'),
    # hazard blocks: diagonal stripes -> pink / black
    'barrier':        ([(0.22, '#0E0614'), (0.42, '#26092A'), (0.62, 'pink'), (0.86, '#FF8CC8')], 512, 'JPEG'),
}
# s1..s6 share one polished-concrete albedo (+ their own baked AO): one solid neon-night colour each
S_COLOURS = {'s1': 'pink_dark', 's2': 'teal', 's3': 'cyan_dark', 's4': 'violet', 's5': 'magenta_wall', 's6': 'cyan'}
for _m, _c in S_COLOURS.items():
    RAMPS[_m] = ([(0.72, mix(_c, 'black', 0.35)), (0.86, _c)], 512, 'JPEG')
AO_SIZE = 512


# ---------------------------------------------------------------- GLB io
def read_glb(path):
    b = open(path, 'rb').read()
    n = struct.unpack('<I', b[12:16])[0]
    j = json.loads(b[20:20 + n])
    off = 20 + n
    bl = struct.unpack('<I', b[off:off + 4])[0]
    return j, b[off + 8:off + 8 + bl]


def write_glb(path, j, bin_):
    js = json.dumps(j, separators=(',', ':')).encode()
    js += b' ' * (-len(js) % 4)
    bin_ += b'\0' * (-len(bin_) % 4)
    total = 12 + 8 + len(js) + 8 + len(bin_)
    with open(path, 'wb') as f:
        f.write(struct.pack('<4sII', b'glTF', 2, total))
        f.write(struct.pack('<I4s', len(js), b'JSON') + js)
        f.write(struct.pack('<I4s', len(bin_), b'BIN\0') + bin_)


def view_bytes(j, bin_, bv):
    v = j['bufferViews'][bv]
    o = v.get('byteOffset', 0)
    return bin_[o:o + v['byteLength']]


# ---------------------------------------------------------------- image helpers (Blender as codec)
def decode(data, mime):
    p = os.path.join(TMP, 'in.' + ('png' if 'png' in mime else 'jpg'))
    open(p, 'wb').write(data)
    im = bpy.data.images.load(p)
    w, h = im.size
    a = np.empty(w * h * 4, np.float32)
    im.pixels.foreach_get(a)
    bpy.data.images.remove(im)
    return np.flipud(a.reshape(h, w, 4))  # top-down rows, sRGB values


def downscale(a, max_size):
    h, w = a.shape[:2]
    f = max(1, max(h, w) // max_size)
    if f == 1:
        return a
    return a[:h - h % f, :w - w % f].reshape(h // f, f, w // f, f, -1).mean(axis=(1, 3))


def encode(a, fmt, quality=88):
    h, w = a.shape[:2]
    im = bpy.data.images.new('enc', w, h, alpha=False)
    rgba = np.ones((h, w, 4), np.float32)
    rgba[..., :3] = np.clip(a[..., :3], 0, 1)
    im.pixels.foreach_set(np.flipud(rgba).ravel())
    im.file_format = fmt
    p = os.path.join(TMP, 'out.' + ('png' if fmt == 'PNG' else 'jpg'))
    im.save(filepath=p, quality=quality)
    bpy.data.images.remove(im)
    return open(p, 'rb').read(), ('image/png' if fmt == 'PNG' else 'image/jpeg')


def gradient_map(a, stops):
    lum = a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722
    xs = [s for s, _ in stops]
    cs = np.stack([col(c) for _, c in stops])
    out = np.empty(a.shape[:2] + (3,), np.float32)
    for ch in range(3):
        out[..., ch] = np.interp(lum, xs, cs[:, ch])
    return out


def recolour(j, bin_):
    """Returns {image index: (bytes, mime)} for replaced images and a list of new images to append,
    each as (bytes, mime, name, texture index to repoint)."""
    replaced, appended, decoded = {}, [], {}
    mats = {m['name']: m for m in j['materials']}

    def src(img):
        if img not in decoded:
            im = j['images'][img]
            decoded[img] = decode(view_bytes(j, bin_, im['bufferView']), im['mimeType'])
        return decoded[img]

    for name, (stops, size, fmt) in RAMPS.items():
        m = mats[name]
        tex = m['pbrMetallicRoughness']['baseColorTexture']['index']
        img = j['textures'][tex]['source']
        out = downscale(gradient_map(src(img), stops), size)
        data = encode(out, fmt)
        if img not in replaced:
            replaced[img] = data
        else:  # shared source image (s1..s6): give this material its own copy
            appended.append(data + (f'{name}_retro', tex))
        print(f'[world] {name:15s} image {img:2d} -> {out.shape[1]}x{out.shape[0]} {fmt} {len(data[0]) // 1024} KB')
    for name, m in mats.items():
        occ = m.get('occlusionTexture')
        if occ:
            img = j['textures'][occ['index']]['source']
            if img not in replaced:
                replaced[img] = encode(downscale(src(img), AO_SIZE), 'JPEG', 90)
    return replaced, appended


# ---------------------------------------------------------------- scene analysis
def physics(o):
    return o.get('data') == 'physics'


class Probe:
    """Ray casts against the visible world (with face materials) and the physics colliders."""

    def __init__(self):
        self.vis, self.vis_mat = self._build(lambda o: not physics(o) and o.name != OCEAN.name, True)
        self.phys, _ = self._build(physics, False)

    @staticmethod
    def _build(pick, with_mats):
        verts, polys, mats = [], [], []
        for o in bpy.data.objects:
            if o.type != 'MESH' or not pick(o):
                continue
            me, mw = o.data, o.matrix_world
            base = len(verts)
            verts.extend(mw @ v.co for v in me.vertices)
            names = [m.name if m else '' for m in me.materials]
            for p in me.polygons:
                polys.append([base + i for i in p.vertices])
                if with_mats:
                    mats.append(names[p.material_index] if names else '')
        return BVHTree.FromPolygons(verts, polys), mats

    def down(self, x, y, z=900.0, tree='vis'):
        """First hit straight down: (z, material, normal z) or None."""
        t = self.vis if tree == 'vis' else self.phys
        loc, nrm, idx, _ = t.ray_cast(Vector((x, y, z)), Vector((0, 0, -1)))
        if loc is None:
            return None
        return loc.z, (self.vis_mat[idx] if tree == 'vis' else ''), abs(nrm.z)


def region_bm(o, mats):
    """World-space bmesh of the faces of `o` using one of `mats`, welded (glTF splits vertices at UV seams)."""
    bm = bmesh.new()
    bm.from_mesh(o.data)
    bm.transform(o.matrix_world)
    names = [m.name if m else '' for m in o.data.materials]
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if names[f.material_index] not in mats], context='FACES')
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.01)
    bm.verts.ensure_lookup_table()
    return bm


def edge_chains(edges):
    """Chain bmesh edges into polylines (lists of Vector); closed loops repeat the first point."""
    adj = defaultdict(list)
    for e in edges:
        for v in e.verts:
            adj[v].append(e)
    used, out = set(), []
    for start in sorted(adj, key=lambda v: (len(adj[v]) != 1, v.index)):
        for e0 in adj[start]:
            if e0 in used:
                continue
            pts, v, e = [start.co.copy()], start, e0
            while e is not None:
                used.add(e)
                v = e.other_vert(v)
                pts.append(v.co.copy())
                e = next((x for x in adj[v] if x not in used), None)
            out.append(pts)
    return out


def simplify(pts, eps=0.06):
    """Ramer-Douglas-Peucker in 3D."""
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    ab = b - a
    best, bi = -1.0, 0
    for i in range(1, len(pts) - 1):
        if ab.length < 1e-6:
            d = (pts[i] - a).length
        else:
            d = (pts[i] - a).cross(ab.normalized()).length
        if d > best:
            best, bi = d, i
    if best <= eps:
        return [a, b]
    return simplify(pts[:bi + 1], eps)[:-1] + simplify(pts[bi:], eps)


def chain_length(pts):
    return sum((q - p).length for p, q in zip(pts, pts[1:]))


# ---------------------------------------------------------------- neon builders
NEON_COLOURS = ['pink', 'cyan', 'yellow']
NEON, GLOW = [], []


def strip_line(pts, colour, w, h, lift, side=None, pad=0.3, inset=0.5):
    """Neon bar (+ glow shell) along a polyline. `side(mid, perp)` returns +1/-1 to shift the bar
    `inset` * w towards that side of the line (e.g. onto the roof or the road)."""
    for p, q in zip(pts, pts[1:]):
        d = q - p
        if d.length < 0.05:
            continue
        mid = (p + q) / 2
        perp = Vector((-d.y, d.x, 0))
        perp = perp.normalized() if perp.length > 1e-6 else Vector((0, 0, 0))
        off = Vector((0, 0, lift))
        if side:
            off += perp * side(mid, perp) * w * inset
        objs = C.neon_strip('neon', mid + off, (d.length + w * 0.9, w, h), colour, pad=pad)
        quat = d.normalized().to_track_quat('X', 'Z')
        for o in objs:
            o.rotation_mode = 'QUATERNION'
            o.rotation_quaternion = quat
        NEON.append(objs[0])
        GLOW.append(objs[1])


def same_level_side(z_tol=0.3, mats=None):
    """Side test: the side where the surface continues at the line's height (and material, if given)."""
    def side(mid, perp):
        for s in (1, -1):
            hit = PROBE.down(mid.x + perp.x * s * 0.6, mid.y + perp.y * s * 0.6, mid.z + 1.0)
            if hit and abs(hit[0] - mid.z) < z_tol and (mats is None or hit[1] in mats):
                return s
        return 0
    return side


def rim_outlines(o, mats, colours, w=0.45, h=0.32):
    """Glowing outline along the top edges of walls: boundary edges of the `mats` region that drop away."""
    bm = region_bm(o, mats)
    keep = []
    for e in bm.edges:
        if not e.is_boundary:
            continue
        a, b = (v.co for v in e.verts)
        mid, d = (a + b) / 2, b - a
        perp = Vector((-d.y, d.x, 0)).normalized()
        drops = 0
        for s in (1, -1):  # 3 m out: catches vertical walls, the sloped court banks and the platform edge
            hit = PROBE.down(mid.x + perp.x * s * 3.0, mid.y + perp.y * s * 3.0, mid.z + 0.5)
            if hit is None or hit[0] < mid.z - 1.0:
                drops += 1
        if drops == 1:
            keep.append(e)
    chains = [simplify(c) for c in edge_chains(keep)]
    chains = [c for c in chains if chain_length(c) > 3.0]
    chains.sort(key=lambda c: -chain_length(c))
    for i, c in enumerate(chains):
        strip_line(c, colours[i % len(colours)], w, h, h / 2, side=same_level_side())
    bm.free()
    return chains


def kerbs(o, mats, colour_fn, w=0.35, h=0.08):
    """Flat neon kerb lines along the edges of a road region, sitting on the road edge."""
    bm = region_bm(o, mats)
    chains = [simplify(c, 0.04) for c in edge_chains([e for e in bm.edges if e.is_boundary])]
    chains = [c for c in chains if chain_length(c) > 6.0]
    chains.sort(key=lambda c: -chain_length(c))
    for i, c in enumerate(chains):
        strip_line(c, colour_fn(i, c), w, h, h / 2 - 0.02, side=same_level_side(0.5, set(mats)), pad=0.25)
    bm.free()
    return chains


def tower_rings(o, colours, w=0.5, h=0.35):
    """Outlines on a tower: around its top (the heli pad) and around its widest platform."""
    mw = o.matrix_world
    ws = [mw @ v.co for v in o.data.vertices]
    zt = max(v.z for v in ws)
    top = [v for v in ws if v.z > zt - 0.05]
    hull = [ws[i] for i in convex_hull_2d([(v.x, v.y) for v in ws])]
    zw = max(v.z for v in ws if any((v.to_2d() - c.to_2d()).length < 0.05 for c in hull))
    for pts, z, colour in ((top, zt, colours[0]), (hull, zw, colours[1])):
        ring = [pts[i] for i in convex_hull_2d([(v.x, v.y) for v in pts])]
        ring = [Vector((v.x, v.y, z)) for v in ring]
        ring.append(ring[0].copy())
        ctr = sum(ring[:-1], Vector()) / (len(ring) - 1)

        def inward(mid, perp, ctr=ctr):
            return 1 if (ctr - mid).dot(perp) > 0 else -1
        strip_line(simplify(ring, 0.05), colour, w, h, h / 2, side=inward)


def ribbon_rails(o, colour, min_len=20.0, w=0.4, h=0.4):
    """Neon rails along both edges of the driving surface of a ribbon ramp (the loop, the mega ramp).
    Faces are grouped across smooth edges; the group facing up the most is the driving surface."""
    bm = region_bm(o, {m.name for m in o.data.materials if m})
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    smooth = lambda e: len(e.link_faces) == 2 and e.calc_face_angle(math.pi) < math.radians(50)
    group = {}
    for f in bm.faces:
        if f in group:
            continue
        gid, stack = len(set(group.values())), [f]
        group[f] = gid
        while stack:
            g = stack.pop()
            for e in g.edges:
                if smooth(e):
                    for k in e.link_faces:
                        if k not in group:
                            group[k] = gid
                            stack.append(k)
    score = defaultdict(float)
    for f in bm.faces:
        score[group[f]] += f.normal.z * f.calc_area()
    top = max(score, key=score.get)
    rail = [e for e in bm.edges if sum(group[f] == top for f in e.link_faces) == 1]
    chains = [simplify(c, 0.05) for c in edge_chains(rail)]
    chains = [c for c in chains if chain_length(c) > min_len]
    for c in chains:
        strip_line(c, colour, w, h, 0.0)
    bm.free()
    return chains


def neon_shell(o, mats, colour, grow=0.04, glow=0.22):
    """Neon copy of part of a mesh (the hoops), pushed out along normals, plus a fatter glow shell."""
    for kind, dist in (('neon', grow), ('glow', glow)):
        bm = region_bm(o, mats)
        bm.normal_update()
        for v in bm.verts:
            v.co += v.normal * dist
        me = bpy.data.meshes.new('shell')
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new('shell', me)
        bpy.context.scene.collection.objects.link(ob)
        C.paint(ob, colour, kind)
        (NEON if kind == 'neon' else GLOW).append(ob)


# ---------------------------------------------------------------- palms
GROUND = {'concrete', 'roof'}
FAST = {'race_track', 'dirt_road', 'runway', 'helipad'}  # keep palms well clear of these
PALMS, COLLIDERS = [], []


def path_segments():
    nodes = {o.name: o for o in bpy.data.objects if o.get('data') == 'pathNode'}
    segs = []
    for o in nodes.values():
        nxt = nodes.get(o.get('nextNode', ''))
        if nxt:
            segs.append((o.matrix_world.translation.to_2d(), nxt.matrix_world.translation.to_2d()))
    return segs


def seg_dist(p, a, b):
    ab = b - a
    t = 0.0 if ab.length_squared < 1e-9 else max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
    return (p - (a + ab * t)).length


EXCLUDE = [  # (xmin, xmax, ymin, ymax) in Blender XY
    (-50, 50, -170, -98),      # foot of the mega ramp: cars land on the south terrace here
    (-170, 170, 108, 175),     # loop ramp run-in / run-out on the north terrace
    (128, 178, -175, 175),     # runway and its take-off corridor
    (-162, -84, -92, -14),     # test ramps and their run-ups
]


def palm_site_ok(x, y, segs, spawns):
    if any(x0 <= x <= x1 and y0 <= y <= y1 for x0, x1, y0, y1 in EXCLUDE):
        return None
    p = Vector((x, y))
    if any((p - s).length < 12.0 for s in spawns):
        return None
    if any(seg_dist(p, a, b) < 9.0 for a, b in segs):
        return None
    c = PROBE.down(x, y)
    if c is None or c[1] not in GROUND or c[2] < 0.98:
        return None
    z0 = c[0]
    ph = PROBE.down(x, y, tree='phys')
    if ph is None or abs(ph[0] - z0) > 0.25:
        return None
    # clear, flat ground of the same kind within 4.5 m (keeps palms off roads, ramps, kerbs, wall lips)
    for r in (2.0, 4.5):
        for k in range(12):
            a = 2 * math.pi * k / 12
            sx, sy = x + r * math.cos(a), y + r * math.sin(a)
            h = PROBE.down(sx, sy)
            if h is None or h[1] not in GROUND or abs(h[0] - z0) > 0.05:
                return None
            hp = PROBE.down(sx, sy, tree='phys')
            if hp is None or abs(hp[0] - z0) > 0.25:
                return None
    for k in range(16):  # 9 m clear of race tracks, the runway and the helipad
        a = 2 * math.pi * k / 16
        h = PROBE.down(x + 9.0 * math.cos(a), y + 9.0 * math.sin(a))
        if h and h[1] in FAST:
            return None
    # "near" something: a road, path, wall or rim within 11 m
    near = False
    for r in (7.0, 11.0):
        for k in range(16):
            a = 2 * math.pi * k / 16
            h = PROBE.down(x + r * math.cos(a), y + r * math.sin(a))
            if h is None or h[1] not in GROUND or abs(h[0] - z0) > 0.5:
                near = True
                break
        if near:
            break
    return z0 if near else None


def frond(bm, top, ang, length, rise, droop, width):
    d = Vector((math.cos(ang), math.sin(ang), 0))
    side = Vector((-d.y, d.x, 0))
    n = 5
    prof = [0.12, 0.8, 1.0, 0.7, 0.08]
    rows = []
    for i in range(n):
        u = i / (n - 1)
        p = top + d * (length * u) + Vector((0, 0, rise * u - droop * u * u))
        w = width * prof[i]
        rows.append((bm.verts.new(p - side * w - Vector((0, 0, 0.18 * w))),
                     bm.verts.new(p + Vector((0, 0, 0.12 * w))),
                     bm.verts.new(p + side * w - Vector((0, 0, 0.18 * w)))))
    for (l0, m0, r0), (l1, m1, r1) in zip(rows, rows[1:]):
        bm.faces.new((l0, l1, m1, m0))
        bm.faces.new((m0, m1, r1, r0))


def build_palm(i, base, rng):
    H = rng.uniform(6.5, 9.5)
    lean_a = rng.uniform(0, 2 * math.pi)
    lean = Vector((math.cos(lean_a), math.sin(lean_a), 0)) * rng.uniform(0.3, 1.2)
    nseg = 6
    pts = [base + lean * ((k / nseg) ** 1.6) + Vector((0, 0, H * k / nseg)) for k in range(nseg + 1)]
    parts = []
    for k in range(nseg):
        p, q = pts[k], pts[k + 1]
        r0 = 0.36 - 0.14 * k / nseg
        seg = C.cylinder(f'palm{i}_t{k}', (p + q) / 2, r0, (q - p).length * 1.04, 'trunk', segs=6, radius2=r0 * 0.78)
        seg.rotation_mode = 'QUATERNION'
        seg.rotation_quaternion = (q - p).normalized().to_track_quat('Z', 'Y')
        parts.append(seg)
    top = pts[-1] + Vector((0, 0, 0.1))
    spin = rng.uniform(0, 2 * math.pi)
    nf = rng.choice((8, 9))
    for tone in ('palm', 'teal'):
        bm = bmesh.new()
        for k in range(nf):
            if (k % 2 == 0) != (tone == 'palm'):
                continue
            frond(bm, top, spin + 2 * math.pi * k / nf + rng.uniform(-0.15, 0.15), rng.uniform(3.6, 4.8) * H / 8,
                  rng.uniform(0.6, 1.2), rng.uniform(2.2, 3.2) * H / 8, rng.uniform(0.75, 0.95))
        me = bpy.data.meshes.new(f'palm{i}_{tone}')
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(me.name, me)
        bpy.context.scene.collection.objects.link(ob)
        parts.append(C.paint(ob, tone))
    nut = C.ellipsoid(f'palm{i}_nuts', top - Vector((0, 0, 0.35)), (0.42, 0.42, 0.3), 'orange', segs=6, rings=4,
                      smooth=False)
    parts.append(nut)
    # invisible trunk collider (glTF Y up; BoxCollider takes half extents from scale, position from translation)
    hh = H * 0.45
    cx, cy = base.x + lean.x * 0.25, base.y + lean.y * 0.25
    COLLIDERS.append({'name': f'palm_collider.{i:03d}', 'translation': [round(cx, 4), round(base.z + hh, 4), round(-cy, 4)],
                      'scale': [0.38, round(hh, 4), 0.38], 'extras': {'data': 'physics', 'type': 'box'}})
    return parts


def place_palms(target=32, spacing=17.0):
    rng = random.Random(SEED)
    segs = path_segments()
    spawns = [o.matrix_world.translation.to_2d() for o in bpy.data.objects if o.get('data') in ('spawn', 'scenario')]
    cands = []
    for gx in range(-206, 207, 3):
        for gy in range(-150, 167, 3):
            x, y = gx + rng.uniform(-1, 1), gy + rng.uniform(-1, 1)
            z0 = palm_site_ok(x, y, segs, spawns)
            if z0 is not None:
                cands.append(Vector((x, y, z0)))
    rng.shuffle(cands)
    chosen = []
    for c in cands:
        if all((c.to_2d() - p.to_2d()).length >= spacing for p in chosen):
            chosen.append(c)
        if len(chosen) >= target:
            break
    print(f'[world] palm sites: {len(cands)} candidates, {len(chosen)} chosen')
    parts = []
    for i, base in enumerate(chosen):
        parts += build_palm(i, base - Vector((0, 0, 0.1)), rng)
    return chosen, parts


# ---------------------------------------------------------------- merge
def merge(j0, b0, replaced, appended, j1, b1, colliders):
    j = json.loads(json.dumps(j0))
    out = bytearray()

    def push(data):
        while len(out) % 4:
            out.append(0)
        o = len(out)
        out.extend(data)
        return o

    img_view = {im['bufferView']: i for i, im in enumerate(j['images'])}
    for i, v in enumerate(j['bufferViews']):
        if i in img_view and img_view[i] in replaced:
            data, mime = replaced[img_view[i]]
            j['images'][img_view[i]]['mimeType'] = mime
        else:
            data = view_bytes(j0, b0, i)
        v['byteOffset'] = push(data)
        v['byteLength'] = len(data)
    for data, mime, name, tex in appended:
        j['bufferViews'].append({'buffer': 0, 'byteOffset': push(data), 'byteLength': len(data)})
        j['images'].append({'bufferView': len(j['bufferViews']) - 1, 'mimeType': mime, 'name': name})
        j['textures'][tex]['source'] = len(j['images']) - 1

    # append the Blender export (neon, glow, palms)
    off = {k: len(j.get(k, [])) for k in ('bufferViews', 'accessors', 'images', 'samplers', 'textures', 'materials',
                                         'meshes', 'nodes')}
    for i, v in enumerate(j1.get('bufferViews', [])):
        nv = dict(v)
        nv['buffer'] = 0
        nv['byteOffset'] = push(view_bytes(j1, b1, i))
        j['bufferViews'].append(nv)
    for a in j1.get('accessors', []):
        a = dict(a)
        a['bufferView'] += off['bufferViews']
        j['accessors'].append(a)
    for im in j1.get('images', []):
        im = dict(im)
        im['bufferView'] += off['bufferViews']
        j['images'].append(im)
    j.setdefault('samplers', []).extend(j1.get('samplers', []))
    for t in j1.get('textures', []):
        t = dict(t)
        if 'source' in t:
            t['source'] += off['images']
        if 'sampler' in t:
            t['sampler'] += off['samplers']
        j['textures'].append(t)

    def fix_tex(d):
        if isinstance(d, dict):
            for k, v in d.items():
                if k.endswith('Texture') and isinstance(v, dict) and 'index' in v:
                    v['index'] += off['textures']
                fix_tex(v)
        elif isinstance(d, list):
            for v in d:
                fix_tex(v)
    for m in j1.get('materials', []):
        m = json.loads(json.dumps(m))
        fix_tex(m)
        j['materials'].append(m)
    for me in j1.get('meshes', []):
        me = json.loads(json.dumps(me))
        for p in me['primitives']:
            p['attributes'] = {k: v + off['accessors'] for k, v in p['attributes'].items()}
            if 'indices' in p:
                p['indices'] += off['accessors']
            if 'material' in p:
                p['material'] += off['materials']
        j['meshes'].append(me)
    roots = []
    for i, n in enumerate(j1.get('nodes', [])):
        n = dict(n)
        if 'mesh' in n:
            n['mesh'] += off['meshes']
        if 'children' in n:
            n['children'] = [c + off['nodes'] for c in n['children']]
        n.pop('extras', None)
        j['nodes'].append(n)
    roots += [r + off['nodes'] for r in j1['scenes'][j1.get('scene', 0)]['nodes']]
    for c in colliders:
        j['nodes'].append(c)
        roots.append(len(j['nodes']) - 1)
    j['scenes'][j.get('scene', 0)]['nodes'].extend(roots)
    used = set(j.get('extensionsUsed', [])) | set(j1.get('extensionsUsed', []))
    if used:
        j['extensionsUsed'] = sorted(used)
    j['buffers'] = [{'byteLength': len(out)}]
    return j, bytes(out)


def check_untouched(j0, j):
    """Every original node is still there with the same name, transform, extras, mesh and children."""
    bad = [i for i, n in enumerate(j0['nodes']) if j['nodes'][i] != n]
    root0 = j0['scenes'][0]['nodes']
    assert j['scenes'][0]['nodes'][:len(root0)] == root0, 'root order changed'
    assert not bad, f'original nodes changed: {bad[:10]}'
    added = [n for n in j['nodes'][len(j0['nodes']):] if n.get('extras')]
    assert all(n['extras'] == {'data': 'physics', 'type': 'box'} for n in added)
    names = [n.get('name') for n in j['nodes']]
    assert len(names) == len(set(names)), 'duplicate node names'
    print(f'[world] check: {len(j0["nodes"])} original nodes identical, {len(added)} palm colliders added')


# ---------------------------------------------------------------- preview
VIEWS = {  # name: (camera type, location, target, lens or ortho scale, light, resolution)
    'top':        ('ORTHO', (0, 8, 400), (0, 8, 0), 440, 'FLAT', (1600, 1250)),
    'west':       ('PERSP', (-60, -130, 70), (-112, -35, 15), 26, 'STUDIO', (1280, 800)),
    'center':     ('PERSP', (40, -95, 55), (0, 0, 15), 26, 'STUDIO', (1280, 800)),
    'east':       ('PERSP', (75, 45, 55), (135, -25, 15), 26, 'STUDIO', (1280, 800)),
    # roughly the in-game top-down camera (~65 deg pitch) at the default spawn, a terrace rim and the oval
    'game_spawn': ('PERSP', (0, -13, 45), (0, 5, 15), 24, 'STUDIO', (1280, 800)),
    'game_rim':   ('PERSP', (-110, -130, 50), (-110, -112, 18), 24, 'STUDIO', (1280, 800)),
    'game_oval':  ('PERSP', (52, 42, 46), (52, 60, 16), 24, 'STUDIO', (1280, 800)),
    'game_palms': ('PERSP', (192, 20, 51), (192, 38, 21), 24, 'STUDIO', (1280, 800)),
    'tower':      ('PERSP', (140, -45, 100), (185, 0, 78), 30, 'STUDIO', (1280, 800)),
}


def preview(glb, out_dir, views=VIEWS):
    C.reset()
    C.import_glb(glb)
    for o in bpy.data.objects:
        if physics(o) or (o.type == 'MESH' and o.data.materials and o.data.materials[0].name == 'glow'):
            o.hide_render = True
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_WORKBENCH'
    sc.view_settings.view_transform = 'Standard'
    sh = sc.display.shading
    sh.color_type = 'TEXTURE'
    sh.show_shadows = False
    sc.world = sc.world or bpy.data.worlds.new('w')
    sc.world.color = (0.03, 0.01, 0.06)
    cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam'))
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.data.clip_end = 5000
    for name, (typ, loc, tgt, lens, light, res) in views.items():
        cam.data.type = typ
        if typ == 'ORTHO':
            cam.data.ortho_scale = lens
        else:
            cam.data.lens = lens
        cam.location = Vector(loc)
        cam.rotation_euler = (Vector(tgt) - cam.location).to_track_quat('-Z', 'Y').to_euler()
        sh.light = light
        sc.render.resolution_x, sc.render.resolution_y = res
        sc.render.filepath = os.path.join(out_dir, f'world_{name}.png')
        bpy.ops.render.render(write_still=True)


# ---------------------------------------------------------------- main
j0, b0 = read_glb(SRC)
replaced, appended = recolour(j0, b0)

C.reset()
C.import_glb(SRC)
OCEAN = next(o for o in bpy.data.objects if o.type == 'MESH' and o.data.materials
             and o.data.materials[0].name == 'ocean')
originals = list(bpy.data.objects)
bpy.context.view_layer.update()
PROBE = Probe()
plane = bpy.data.objects['Plane']

# 1. glowing outlines along the top of the court walls / platform edge (the terrace is the "roof")
rims = rim_outlines(plane, {'roof'}, ['pink', 'cyan', 'yellow', 'cyan'])
# 2. the two towers (heli pads on top) and the hoops
for i, name in enumerate(('Cube.392', 'Cube.289')):
    tower_rings(bpy.data.objects[name], [['cyan', 'pink'], ['pink', 'cyan']][i])
hoops = sorted((o for o in originals if o.name.startswith('Torus')), key=lambda o: o.name)
for i, o in enumerate(hoops):
    neon_shell(o, {'plaster'}, NEON_COLOURS[i % 3])
# 3. bridge deck outline (runway faces on top of the viaduct)
deck = kerbs(bpy.data.objects['Cube.476'], ['runway'], lambda i, c: 'yellow', w=0.4, h=0.25)
# 4. rails along both edges of the loop ramp and the mega ramp
loop = ribbon_rails(bpy.data.objects['BezierCircle.021'], 'cyan')
mega = ribbon_rails(bpy.data.objects['Cube.808'], 'pink')
# 5. neon kerbs: oval track (outer pink, inner cyan), figure-8 dirt track (yellow), runway (cyan)
oval = kerbs(plane, ['race_track'], lambda i, c: 'pink' if i == 0 else 'cyan')
fig8 = kerbs(bpy.data.objects['BezierCircle'], ['dirt_road'], lambda i, c: 'yellow')
runway = kerbs(bpy.data.objects['Cube.009'], ['runway'], lambda i, c: 'cyan')
print(f'[world] neon: {len(rims)} rim outlines, 4 tower rings, {len(hoops)} hoops, deck {len(deck)}, '
      f'loop rails {len(loop)}, mega ramp rails {len(mega)}, kerbs oval {len(oval)} / figure-8 {len(fig8)} / '
      f'runway {len(runway)}; {len(NEON)} neon parts')

# 6. palms
sites, palm_parts = place_palms()
PALMS.extend(palm_parts)

# keep only the new objects and export them
for o in originals:
    bpy.data.objects.remove(o)
neon_obj = C.join(NEON, 'NeonLines')
glow_obj = C.join(GLOW, 'NeonGlow')
palm_obj = C.join(PALMS, 'Palms')
for o in (neon_obj, glow_obj, palm_obj):
    for k in list(o.keys()):
        del o[k]
for o in (neon_obj, glow_obj):  # unlit materials ignore normals: smooth = shared vertices = a ~3x smaller export
    o.data.polygons.foreach_set('use_smooth', [True] * len(o.data.polygons))
    o.data.update()
extra_glb = os.path.join(TMP, 'extra.glb')
C.export_glb(extra_glb)
j1, b1 = read_glb(extra_glb)

j, b = merge(j0, b0, replaced, appended, j1, b1, COLLIDERS)
check_untouched(j0, j)
write_glb(OUT, j, b)
print(f'[world] wrote {OUT}: {os.path.getsize(OUT) / 1e6:.2f} MB (was {os.path.getsize(SRC) / 1e6:.2f} MB), '
      f'{len(j["nodes"])} nodes, {len(COLLIDERS)} palms')

shutil.rmtree(TMP, ignore_errors=True)
if PREVIEW:
    os.makedirs(PREVIEW, exist_ok=True)
    preview(OUT, PREVIEW)
