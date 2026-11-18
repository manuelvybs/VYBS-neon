"""Shared helpers for the retro-neon asset scripts (run inside Blender 5.x).

Conventions every asset follows:
- One 512x512 atlas image (palette swatches on top, visor art at the bottom).
- Material "retro"  -> lit (Sketchbook turns it into MeshPhongMaterial).
- Material "neon"   -> unlit full-brightness (MeshBasicMaterial).
- Material "glow"   -> additive transparent halo around neon parts.
- Material "visor"  -> lit, alpha-cutout (astronaut visor).
Each part is painted by pointing all its UVs at one palette swatch.
"""
import bpy, bmesh, math, os
import numpy as np
from mathutils import Vector, Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
ATLAS_PATH = os.path.join(HERE, 'atlas.png')

PALETTE = [
    ('suit', '#E2F7FA'), ('suit_shade', '#B4DAE8'), ('sole', '#A3CAD8'), ('indigo', '#3B44C4'),
    ('deep', '#23299C'), ('glint', '#6B73D3'), ('lav', '#A4A9EA'), ('yellow', '#FFE45C'),
    ('star_yellow', '#FDD843'), ('orange', '#FF7B3A'), ('pink', '#FF2E97'), ('cyan', '#19E3FF'),
    ('violet', '#B44CFF'), ('white', '#F5F0FF'), ('black', '#120818'), ('glass', '#2D1B5E'),
    ('chrome', '#C9C3E6'), ('asphalt', '#1A0D30'), ('walk', '#2E1A52'), ('roof', '#3A2160'),
    ('purple_wall', '#6A2A9C'), ('magenta_wall', '#B01E6E'), ('teal', '#0F7F74'), ('palm', '#0E8F7A'),
    ('trunk', '#7A4A6E'), ('sand', '#FFB38A'), ('water', '#0B6F8A'), ('dark', '#3D2466'),
    ('tire', '#1A1424'), ('hub', '#C9C3E6'), ('pink_dark', '#C21E78'), ('cyan_dark', '#0E8FB0'),
]
SWATCH = {name: i for i, (name, _) in enumerate(PALETTE)}
COLS, CELL = 8, 64  # 8 x 4 swatches of 64px in the top half


def hex_rgb(h):
    h = h.lstrip('#')
    return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]


def swatch_uv(name):
    i = SWATCH[name]
    col, row = i % COLS, i // COLS
    return ((col + 0.5) / COLS, 1.0 - (row + 0.5) * CELL / 512.0)


# ---------- atlas ----------
def _sparkle(xx, yy, cx, cy, sx, sy, p=0.62):
    dx = np.abs(xx - cx) / sx
    dy = np.abs(yy - cy) / sy
    return (dx ** p + dy ** p) <= 1.0


def _rrect(xx, yy, x0, y0, x1, y1, r):
    cx = np.clip(xx, x0 + r, x1 - r)
    cy = np.clip(yy, y0 + r, y1 - r)
    return ((xx - cx) ** 2 + (yy - cy) ** 2 <= r * r) & (xx >= x0) & (xx <= x1) & (yy >= y0) & (yy <= y1)


def build_atlas():
    """Returns RGBA float array, top-down rows, 512x512."""
    img = np.zeros((512, 512, 4), np.float32)
    for name, hx in PALETTE:
        i = SWATCH[name]
        col, row = i % COLS, i // COLS
        img[row * CELL:(row + 1) * CELL, col * CELL:(col + 1) * CELL, :3] = hex_rgb(hx)
        img[row * CELL:(row + 1) * CELL, col * CELL:(col + 1) * CELL, 3] = 1.0
    # Visor art in rows 256..511 (supersampled 3x for clean edges)
    ss = 3
    H, W = 256 * ss, 512 * ss
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    xx = (xx + 0.5) / ss
    yy = (yy + 0.5) / ss
    acc = np.zeros((H, W, 4), np.float32)
    outer = _rrect(xx, yy, 6, 5, 506, 251, 84)
    inner = _rrect(xx, yy, 24, 19, 488, 237, 70)
    acc[outer] = hex_rgb('#A4A9EA') + [1]
    acc[inner] = hex_rgb('#3B44C4') + [1]
    e1 = ((xx - 300) / 420) ** 2 + ((yy - 370) / 178) ** 2 <= 1
    e2 = ((xx - 330) / 420) ** 2 + ((yy - 398) / 186) ** 2 <= 1
    acc[inner & e1 & ~e2] = hex_rgb('#6B73D3') + [1]
    # sparkles: horizontal squash keeps them round on the curved visor
    acc[inner & _sparkle(xx, yy, 205, 117, 92 * 0.8, 70)] = hex_rgb('#FFFFFF') + [1]
    acc[inner & _sparkle(xx, yy, 345, 151, 44 * 0.8, 33)] = hex_rgb('#FDD843') + [1]
    acc = acc.reshape(256, ss, 512, ss, 4).mean(axis=(1, 3))
    img[256:512] = acc
    return img


def atlas_image():
    img = bpy.data.images.get('retro_atlas')
    if img:
        return img
    arr = build_atlas()
    img = bpy.data.images.new('retro_atlas', 512, 512, alpha=True)
    img.pixels.foreach_set(np.flipud(arr).ravel())
    img.filepath_raw = ATLAS_PATH
    img.file_format = 'PNG'
    img.save()
    img.pack()
    return img


def material(kind):
    """kind in retro | neon | glow | visor"""
    m = bpy.data.materials.get(kind)
    if m:
        return m
    m = bpy.data.materials.new(kind)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get('Principled BSDF')
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = atlas_image()
    tex.interpolation = 'Closest'
    nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    bsdf.inputs['Roughness'].default_value = 1.0
    bsdf.inputs['Metallic'].default_value = 0.0
    if kind == 'visor':
        nt.links.new(tex.outputs['Alpha'], bsdf.inputs['Alpha'])
        try:
            m.blend_method = 'CLIP'
        except Exception:
            pass
        m.alpha_threshold = 0.5
    if kind == 'glow':
        bsdf.inputs['Alpha'].default_value = 0.35
        try:
            m.blend_method = 'BLEND'
        except Exception:
            pass
        m.surface_render_method = 'BLENDED'
    return m


def paint(obj, color, kind='retro'):
    """Assign material kind and point every UV at the colour swatch."""
    me = obj.data
    me.materials.clear()
    me.materials.append(material(kind))
    if not me.uv_layers:
        me.uv_layers.new(name='UVMap')
    u, v = swatch_uv(color)
    uv = me.uv_layers.active.data
    for loop in uv:
        loop.uv = (u, v)
    return obj


# ---------- primitives (all return a mesh object linked to the scene) ----------
def _link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _new_obj(name, bm):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return _link(bpy.data.objects.new(name, me))


def ellipsoid(name, center, radii, color, kind='retro', segs=24, rings=16, smooth=True):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=1.0)
    bmesh.ops.scale(bm, vec=Vector(radii), verts=bm.verts)
    for f in bm.faces:
        f.smooth = smooth
    o = _new_obj(name, bm)
    o.location = Vector(center)
    return paint(o, color, kind)


def cylinder(name, center, radius, depth, color, kind='retro', segs=24, axis='Z', radius2=None, smooth=False):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=radius,
                          radius2=radius if radius2 is None else radius2, depth=depth)
    if axis == 'X':
        bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'))
    elif axis == 'Y':
        bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'X'))
    for f in bm.faces:
        f.smooth = smooth
    o = _new_obj(name, bm)
    o.location = Vector(center)
    return paint(o, color, kind)


def box(name, center, size, color, kind='retro', bevel=0.0, segments=2):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    if bevel > 0:
        bmesh.ops.bevel(bm, geom=list(bm.edges) + list(bm.verts), offset=min(bevel, min(size) * 0.49),
                        segments=segments, affect='EDGES', profile=0.5)
    o = _new_obj(name, bm)
    o.location = Vector(center)
    return paint(o, color, kind)


def torus(name, center, major, minor, color, kind='retro', segs=32, ring_segs=12):
    bm = bmesh.new()
    verts = []
    for i in range(segs):
        a = 2 * math.pi * i / segs
        ring = []
        for j in range(ring_segs):
            b = 2 * math.pi * j / ring_segs
            r = major + minor * math.cos(b)
            ring.append(bm.verts.new((r * math.cos(a), r * math.sin(a), minor * math.sin(b))))
        verts.append(ring)
    for i in range(segs):
        for j in range(ring_segs):
            f = bm.faces.new((verts[i][j], verts[(i + 1) % segs][j], verts[(i + 1) % segs][(j + 1) % ring_segs],
                              verts[i][(j + 1) % ring_segs]))
            f.smooth = True
    o = _new_obj(name, bm)
    o.location = Vector(center)
    return paint(o, color, kind)


def prism(name, profile_yz, width, center_x, color, kind='retro'):
    """Extrude a side profile (list of (y, z) in Blender coords) across X by width."""
    bm = bmesh.new()
    left = [bm.verts.new((center_x - width / 2, y, z)) for y, z in profile_yz]
    right = [bm.verts.new((center_x + width / 2, y, z)) for y, z in profile_yz]
    n = len(profile_yz)
    bm.faces.new(list(reversed(left)))
    bm.faces.new(right)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((left[i], left[j], right[j], right[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return paint(_new_obj(name, bm), color, kind)


def neon_strip(name, center, size, color, glow=True, pad=0.06):
    """Unlit neon bar plus an additive halo shell."""
    objs = [box(name, center, size, color, 'neon')]
    if glow:
        objs.append(box(name + '_glow', center, [s + pad for s in size], color, 'glow'))
    return objs


def join(objs, name):
    bpy.ops.object.select_all(action='DESELECT')
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    objs[0].name = name
    objs[0].data.name = name
    return objs[0]


def group_by_material(objs, name):
    """Join parts that share a material kind; returns list of joined objects."""
    by = {}
    for o in objs:
        by.setdefault(o.data.materials[0].name, []).append(o)
    return [join(v, f'{name}_{k}') if len(v) > 1 else v[0] for k, v in by.items()]


# ---------- io ----------
def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_glb(path):
    bpy.ops.import_scene.gltf(filepath=path)
    for o in list(bpy.data.objects):
        if o.name.startswith('Icosphere') and o.parent is None and o.users_collection and not o.children:
            bpy.data.objects.remove(o)


def export_glb(path, animations=False):
    kw = dict(filepath=path, export_format='GLB', export_extras=True, export_yup=True, export_apply=True,
              export_texcoords=True, export_normals=True, export_materials='EXPORT', export_cameras=False,
              export_lights=False, use_selection=False)
    if animations:
        kw.update(export_animations=True, export_animation_mode='ACTIONS', export_skins=True,
                  export_force_sampling=True, export_optimize_animation_size=False,
                  export_def_bones=False, export_anim_single_armature=True)
    else:
        kw.update(export_animations=False, export_skins=False)
    bpy.ops.export_scene.gltf(**kw)


def render_preview(path, target_objs=None, size=(640, 480), cam_dir=(1.0, -1.4, 0.8), lens=50):
    """Quick Workbench render with flat texture colours for visual checks."""
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_WORKBENCH'
    sh = sc.display.shading
    sh.light = 'STUDIO'
    sh.color_type = 'TEXTURE'
    sh.show_shadows = False
    sc.render.resolution_x, sc.render.resolution_y = size
    sc.render.film_transparent = False
    sc.world = sc.world or bpy.data.worlds.new('w')
    objs = target_objs or [o for o in sc.objects if o.type == 'MESH' and o.visible_get()]
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    mn = Vector([min(p[i] for p in pts) for i in range(3)])
    mx = Vector([max(p[i] for p in pts) for i in range(3)])
    ctr = (mn + mx) / 2
    rad = (mx - mn).length / 2
    cam_data = bpy.data.cameras.new('prev_cam')
    cam_data.lens = lens
    cam = bpy.data.objects.new('prev_cam', cam_data)
    sc.collection.objects.link(cam)
    d = Vector(cam_dir).normalized()
    dist = rad / math.tan(cam_data.angle / 2) * 1.15
    cam.location = ctr + d * dist
    cam.rotation_euler = (ctr - cam.location).to_track_quat('-Z', 'Y').to_euler()
    sc.camera = cam
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam)
