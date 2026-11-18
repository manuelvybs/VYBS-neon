"""Build the astronaut hero on the original boxman skeleton.

Run: Blender -b --factory-startup -P tools/retro/astronaut.py -- <original boxman.glb> <out.glb> [preview_dir]
Every part is a rigid mesh parented to one bone, so all 34 boxman animations keep working.
Blender coords: Z up, the character faces -Y.
"""
import bpy, bmesh, sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
from mathutils import Vector

argv = sys.argv[sys.argv.index('--') + 1:]
SRC, OUT = argv[0], argv[1]
PREVIEW = argv[2] if len(argv) > 2 else None

C.reset()
C.import_glb(SRC)
arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
if arm.animation_data:
    arm.animation_data.action = None
arm.data.pose_position = 'REST'
for o in list(bpy.data.objects):
    if o.type == 'MESH':
        bpy.data.objects.remove(o)
bpy.context.view_layer.update()

HELMET_C = Vector((0, 0, 0.86))
HELMET_R = Vector((0.232, 0.222, 0.212))


def visor():
    """Curved patch on the helmet front, UV-mapped to the visor art (atlas bottom half)."""
    nu, nv = 36, 24
    p0, p1 = -0.9, 0.9          # yaw range (rad), 0 = straight ahead (-Y)
    e0, e1 = -0.52, 0.62        # pitch range (rad)
    r = Vector((HELMET_R.x * 1.012, HELMET_R.y * 1.012, HELMET_R.z * 1.012))
    bm = bmesh.new()
    uv_l = bm.loops.layers.uv.new('UVMap')
    grid = []
    for j in range(nv + 1):
        row = []
        e = e0 + (e1 - e0) * j / nv
        for i in range(nu + 1):
            p = p0 + (p1 - p0) * i / nu
            d = Vector((math.sin(p) * math.cos(e), -math.cos(p) * math.cos(e), math.sin(e)))
            row.append((bm.verts.new(HELMET_C + Vector((d.x * r.x, d.y * r.y, d.z * r.z))), i / nu, 0.5 * j / nv))
        grid.append(row)
    for j in range(nv):
        for i in range(nu):
            a, b, c, d = grid[j][i], grid[j][i + 1], grid[j + 1][i + 1], grid[j + 1][i]
            f = bm.faces.new((a[0], b[0], c[0], d[0]))
            f.smooth = True
            for loop, src in zip(f.loops, (a, b, c, d)):
                loop[uv_l].uv = (src[1], src[2])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    # normals must face outward (towards -Y)
    for f in bm.faces:
        if f.normal.y > 0:
            f.normal_flip()
    me = bpy.data.meshes.new('visor')
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new('visor', me)
    bpy.context.scene.collection.objects.link(o)
    me.materials.append(C.material('visor'))
    return o


parts = {}  # bone -> list of objects


def add(bone, obj):
    parts.setdefault(bone, []).append(obj)
    return obj


E = C.ellipsoid
# Head: helmet + visor
add('head', E('helmet', HELMET_C, HELMET_R, 'suit', segs=32, rings=20))
add('head', visor())
# Upper body: chest, collar, chest panel, backpack
add('body_upper', E('chest', (0, 0, 0.53), (0.165, 0.13, 0.13), 'suit'))
add('body_upper', C.torus('collar', (0, 0, 0.638), 0.128, 0.032, 'sole'))
add('body_upper', C.box('chest_panel', (0, -0.118, 0.515), (0.13, 0.04, 0.11), 'indigo', bevel=0.025, segments=3))
add('body_upper', C.box('backpack', (0, 0.148, 0.52), (0.21, 0.085, 0.21), 'indigo', bevel=0.035, segments=3))
add('body_upper', C.box('backpack_slot', (0, 0.193, 0.575), (0.095, 0.012, 0.024), 'lav', bevel=0.01, segments=2))
# Lower body: hips + belt
add('body_lower', E('hips', (0, 0, 0.405), (0.158, 0.124, 0.11), 'suit'))
belt = C.cylinder('belt', (0, 0, 0.36), 0.158, 0.036, 'indigo', segs=32, smooth=True)
belt.scale = (1.0, 0.8, 1.0)
add('body_lower', belt)
# Arms
for side, sx in (('L', 1), ('R', -1)):
    add(f'arm_upper.{side}', E(f'upper_arm_{side}', (sx * 0.205, 0, 0.6), (0.062, 0.062, 0.092), 'suit'))
    add(f'arm_lower.{side}', E(f'lower_arm_{side}', (sx * 0.207, 0, 0.452), (0.058, 0.058, 0.08), 'suit'))
    add(f'arm_lower.{side}', E(f'mitten_{side}', (sx * 0.21, -0.006, 0.355), (0.064, 0.058, 0.062), 'suit'))
    thumb = E(f'thumb_{side}', (sx * 0.19, -0.05, 0.382), (0.022, 0.022, 0.034), 'suit', segs=12, rings=8)
    thumb.rotation_euler = (0.4, 0, -sx * 0.4)
    add(f'arm_lower.{side}', thumb)
    # cuff ring where glove meets sleeve
    add(f'arm_lower.{side}', C.torus(f'cuff_{side}', (sx * 0.209, -0.003, 0.398), 0.05, 0.012, 'suit_shade', segs=20, ring_segs=8))
# Legs
for side, sx in (('L', 1), ('R', -1)):
    add(f'leg_upper.{side}', E(f'thigh_{side}', (sx * 0.072, 0, 0.27), (0.072, 0.072, 0.088), 'suit'))
    add(f'leg_lower.{side}', E(f'shin_{side}', (sx * 0.072, -0.008, 0.12), (0.072, 0.078, 0.085), 'suit'))
    sole = C.cylinder(f'sole_{side}', (sx * 0.072, -0.016, 0.032), 0.07, 0.034, 'sole', segs=24, smooth=True)
    sole.scale = (1.0, 1.28, 1.0)
    add(f'leg_lower.{side}', sole)

# Apply object transforms so joins keep geometry, then join per bone + material and parent to the bone.
bpy.context.view_layer.update()
for bone, objs in parts.items():
    for o in objs:
        bpy.ops.object.select_all(action='DESELECT')
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    joined = C.group_by_material(objs, bone.replace('.', '_'))
    for o in joined:
        mw = o.matrix_world.copy()
        o.parent = arm
        o.parent_type = 'BONE'
        o.parent_bone = bone
        o.matrix_world = mw

arm.data.pose_position = 'POSE'
bpy.context.view_layer.update()

if PREVIEW:
    os.makedirs(PREVIEW, exist_ok=True)
    arm.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    for nm, d in (('front', (0.35, -1.0, 0.25)), ('back', (-0.5, 1.0, 0.3)), ('side', (1.0, -0.15, 0.15))):
        C.render_preview(os.path.join(PREVIEW, f'astro_{nm}.png'), size=(480, 560), cam_dir=d)
    # a posed frame to prove parts follow the bones
    act = bpy.data.actions.get('run') or bpy.data.actions[0]
    arm.animation_data_create()
    arm.animation_data.action = act
    try:
        slot = act.slots[0]
        arm.animation_data.action_slot = slot
    except Exception:
        pass
    arm.data.pose_position = 'POSE'
    bpy.context.scene.frame_set(int(act.frame_range[0] + (act.frame_range[1] - act.frame_range[0]) * 0.35))
    C.render_preview(os.path.join(PREVIEW, 'astro_run.png'), size=(480, 560), cam_dir=(1.0, -0.6, 0.25))
    arm.animation_data.action = None
    arm.data.pose_position = 'POSE'

C.export_glb(OUT, animations=True)
print('ASTRONAUT_EXPORTED', OUT)
