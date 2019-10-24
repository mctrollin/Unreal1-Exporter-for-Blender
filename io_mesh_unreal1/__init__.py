from struct import pack, calcsize
from time import strftime
from shutil import rmtree
import os
import bpy
import bmesh
import bpy_extras
from bpy_extras.io_utils import (
        ImportHelper,
        ExportHelper,
        orientation_helper,
        axis_conversion,
        )
from bpy.types import (
        Operator,
        OperatorFileListElement,
        )

bl_info = {
    "name": "Export to Unreal Mesh (_a.3d, _d.3d)",
    "author": "Till - rollin - Maginot. Based on work form David Townsend (Legend Entertainment), Steve Tack, Gustavo6046",
    "category": "Import-Export",
    "description": "Unreal Engine 1 model exporter",
    "version": (0, 3, 0),
    "blender": (2, 80, 0),
    "location": "File > Import-Export > Unreal1",
}


CONST_EXPORT_FORMAT_UNREAL1 = "UNREAL1"
CONST_EXPORT_FORMAT_DEUSEX = "DEUSEX"


def log(file_, logging, print_it=False):
    if print_it:
        print("[{}] {}".format(strftime("%I:%M:%S"), logging))

    return file_.write("[{}] {}\n".format(strftime("%I:%M:%S"), logging))

def ensure_dir(dirpath):
    # make sure directory exists (if not, create it)
    if not os.path.isdir(dirpath):
        os.makedirs(dirpath)      
    print("Dir ensured: " + dirpath)

def check_mesh(mesh_obj, scale):
    # take export-scale into account
    maxv = 128 / scale
    # make sure all vertices are in valid range
    for i, v in enumerate(mesh_obj.data.vertices):
        if -maxv >= v.co[0] or maxv <= v.co[0]:
            print("Vertex ({}) x-position is out of bounds: {} (max +/-128 units)".format(i, v.co))
            return False
        if -maxv >= v.co[1] or maxv <= v.co[1]:
            print("Vertex ({}) y-position is out of bounds: {} (max +/-128 units)".format(i, v.co))
            return False
        if -maxv >= v.co[2] or maxv <= v.co[2]:
            print("Vertex ({}) z-position is out of bounds: {} (max +/-128 units)".format(i, v.co))
            return False
    return True

# as unsigned long
# Important: raw coordinate values may never extend +/- 128
def enc_vert_unreal(coord):
    return int(
            ( int(coord[0] * 8.0) & int("0x7ff", 16) ) | 
          ( ( int(coord[1] * 8.0) & int("0x7ff", 16) ) << 11 ) |
          ( ( int(coord[2] * 4.0) & int("0x3ff", 16) ) << 22 ) )

# as unsigned long 
# Important: raw coordinate values may never extend +/- 128
def enc_vert_deusex(coord):
    return int(
            ( int(coord[0] * 256.0) & int("0xffff", 16) ) | 
          ( ( int(coord[1] * 256.0) & int("0xffff", 16) ) << 16 ) |
          ( ( int(coord[2] * 256.0) & int("0xffff", 16) ) << 32 ) )

def get_bmesh_snapshot(meshobj_org, flip_x, flip_y, flip_z):
    # https://docs.blender.org/api/blender2.8/bpy.types.Depsgraph.html
    # evaluate dependency graph of selected object
    depsgraph = bpy.context.evaluated_depsgraph_get()
    # get object with dependency graph applied
    meshobj_eval = meshobj_org.evaluated_get(depsgraph)

    # Get a BMesh representation from evaluated mesh
    bm = bmesh.new()
    bm.from_mesh(meshobj_eval.data)

    # triangulated bmesh: always make sure the mesh is triangulated or the exporter will fail
    bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method='SHORT_EDGE', ngon_method='BEAUTY')
    
    if True == flip_x:
        for v in bm.verts:
            v.co.x *= -1
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    if True == flip_y:
        for v in bm.verts:
            v.co.y *= -1
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    if True == flip_z:
        for v in bm.verts:
            v.co.z *= -1
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])

    return bm   

def clear_bmesh_snapshot(bmesh_obj):
    bmesh_obj.free()

def get_jmesh_type(meshobj_org, mat_idx):
    if 0 > mat_idx or len(meshobj_org.material_slots) <= mat_idx:
        return 0 # default

    material = meshobj_org.material_slots[mat_idx]
    # print ("Material Name: {}".format(material.name))

    mat_name = material.name.lower()
    #  DeusEx:
    #   0  "SKIN" (Normal)
    if '(skin)' in mat_name: return 0
    #   1  "TWOSIDEDNORM"
    if '(twosidednorm)' in mat_name: return 1
    #   2  "TRANSLUCENT"
    if '(translucent)' in mat_name: return 2
    #   3  "TWOSIDED"
    if '(twosided)' in mat_name: return 3
    #   8  "WEAPON"
    if '(weapon)' in mat_name: return 8
    #   16 "UNLIT"
    if '(unlit)' in mat_name: return 16
    #   32 "FLAT"
    if '(flat)' in mat_name: return 32
    #   64 "ENVMAPPED"
    if '(envmapped)' in mat_name: return 64

    print ("Unknown material identifier in material name: {}".format(mat_name))
    return 0

# sets the current frame
def set_frame(f):
    bpy.context.scene.frame_set(f)

# returns the current frame
def get_frame():
    return bpy.context.scene.frame_current

# advance current frame by 1
def advance_frame():
    set_frame(get_frame() + 1)


class UnrealMeshExport(Operator):
    bl_idname = "export_mesh.unreal1"
    bl_label = "Export to Unreal format (_a.3d, _d.3d)"

    #properties definition
    p_path_export: bpy.props.StringProperty(
        name="Export Path", 
        description="The path to your Unreal Engine 1 game (without a backslash in the end!).", 
        default="C:\\UnrealTournament",
        subtype="DIR_PATH", 
        )
    p_package_name: bpy.props.StringProperty(
        name="Package Name", 
        description="The name of the package to contain the mesh and the actor.",
        default="MyPackage", 
        )
    p_mesh_name: bpy.props.StringProperty(
        name="Mesh Name", 
        description="Name of the mesh to export",
        default="MyMesh",
        )
    p_scale: bpy.props.FloatProperty(
        name="Scale", 
        description="Scale applied to the model on export",
        default=1, 
        min=0.00001, 
        max=100000, 
        )
    p_flip_model_x: bpy.props.BoolProperty(
        name="Flip Model: X", 
        description="Flip Model on YZ Plane",
        default=False, 
        )
    p_flip_model_y: bpy.props.BoolProperty(
        name="Flip Model: Y", 
        description="Flip Model on XZ Plane",
        default=False, 
        )
    p_flip_model_z: bpy.props.BoolProperty(
        name="Flip Model: Z", 
        description="Flip Model on XY Plane",
        default=False, 
        )
    p_flip_uv_u: bpy.props.BoolProperty(
        name="Flip UVs: U", 
        description="Flip UVs horizontally",
        default=False, 
        )
    p_flip_uv_v: bpy.props.BoolProperty(
        name="Flip UVs: V", 
        description="Flip UVs vertically",
        default=True, 
        )
    p_export_format_type: bpy.props.EnumProperty(
            name="Export Format",
            default = CONST_EXPORT_FORMAT_UNREAL1,
            items = [
                (CONST_EXPORT_FORMAT_UNREAL1 , "Unreal1 / UT" , "This is for Unreal1"),
                (CONST_EXPORT_FORMAT_DEUSEX, "DeusEx", "This is for DeusEx")
            ],
        )

    def execute(self, context):

        # prepare vars ---------------------------
        mesh_obj = context.object
        frame_initial = get_frame()
        frame_exp_start = bpy.context.scene.frame_start
        frame_exp_end = bpy.context.scene.frame_end
        frames_exp_count = frame_exp_end - frame_exp_start + 1

        # read properties ---------------------------
        mesh_name = self.p_mesh_name
        class_name = self.p_mesh_name
        path_export = self.p_path_export
        package_name = self.p_package_name
        mesh_scale = self.p_scale
        flip_model_x = self.p_flip_model_x
        flip_model_y = self.p_flip_model_y
        flip_model_z = self.p_flip_model_z
        flip_uv_u = self.p_flip_uv_u
        flip_uv_v = self.p_flip_uv_v
        export_format_type = self.p_export_format_type

        # checks ---------------------------
        if 0 >= frames_exp_count:
            self.report({'ERROR'}, "Invalid frame range: from:{} to:{} range:{}".format(frame_exp_start, frame_exp_end, frames_exp_count))
            return {'CANCELLED'}

        if None == context.object or 'MESH' != context.object.type:
            self.report({'ERROR'}, "No valid mesh object selected")
            return {'CANCELLED'}

        if False == check_mesh(mesh_obj, mesh_scale):
            self.report({'ERROR'}, "Mesh vertex coordinate(s) out of bounds (max < -/+ 128)")
            return {'CANCELLED'}

        print("Package Name: {}".format(package_name))
        print("Export Path: {}".format(path_export))

        # prepare directories ---------------------------
        ensure_dir(path_export + "\\{}\\".format(package_name))
        ensure_dir(path_export + "\\{}\\Models\\".format(package_name))
        ensure_dir(path_export + "\\{}\\Help\\".format(package_name))
        ensure_dir(path_export + "\\{}\\Skins\\".format(package_name))
        ensure_dir(path_export + "\\{}\\Classes\\".format(package_name))


        # write log file: log.txt
        with open(path_export + "\\{}\\Help\\log.txt".format(package_name), "w") as log_file:
            log(log_file, "Start Export:", True)
            log(log_file, "  Package Name: {}".format(package_name))
            log(log_file, "  Export Path: {}".format(path_export))
            log(log_file, "  Export Format: {}".format(export_format_type))

            # --------------------------------------------------
            # write vertex animation file: _a.3d
            log(log_file, "Writing aniv file ##################################", True)
            with open(path_export + "\\{}\\Models\\{}_a.3d".format(package_name, mesh_name), "wb") as Aniv_File:
                # write Aniv_File header:
                log(log_file, "  Writing aniv file header:", True)
               
                vert_data_type = 'L'
                if CONST_EXPORT_FORMAT_DEUSEX == export_format_type:
                    vert_data_type = 'Q'
                
                log(log_file, "    vert_data_type: {} size: {}".format(vert_data_type, calcsize(vert_data_type)), True)

                # write number of frames (as short 2-Bytes)
                Aniv_File.write(pack("=h", frames_exp_count))
                # write framesize (data size per frame: vertex count * single vertex data size (==unsigned long 8-Bytes)) (as short 2-Bytes)
                Aniv_File.write(pack("=h", len(mesh_obj.data.vertices) * calcsize(vert_data_type)))
               
                log(log_file, "  Writing aniv file body!", True)
                # set frame-pos to start frame
                set_frame(frame_exp_start)
                
                # for every frame ...
                while get_frame() <= frame_exp_end:
                    log(log_file, u"    Frame {} :".format(get_frame()))

                    # prepare export mesh snapshot
                    to_exp_bmesh_snap_f = get_bmesh_snapshot(mesh_obj, flip_model_x, flip_model_y, flip_model_z)

                    # for every vertex ...
                    for i, v in enumerate(to_exp_bmesh_snap_f.verts):
                        # get vertex coordinate and apply scale from property
                        vert_coord = [a * mesh_scale for a in v.co]
                        # encode vertex coordinate into a single value
                        vert_coord_encoded = 0
                        if CONST_EXPORT_FORMAT_DEUSEX == export_format_type:
                            vert_coord_encoded = enc_vert_deusex(vert_coord)
                        else:
                            vert_coord_encoded = enc_vert_unreal(vert_coord)
                        log(log_file, u"      Vertex {} Position: raw=({},{},{}) -> encoded=({})".format(i, vert_coord[0], vert_coord[1], vert_coord[2], vert_coord_encoded))
                        # write encoded vertex coordinate (as unsigned long 8-Bytes)
                        Aniv_File.write(pack('=' + vert_data_type, vert_coord_encoded))

                    # clear mesh snapshot
                    clear_bmesh_snapshot(to_exp_bmesh_snap_f)

                    # advance frame-pos by one frame
                    advance_frame()

                # reset frame-pos to initial position
                set_frame(frame_initial)



            # --------------------------------------------------
            # write mesh data file: _d.3d
            log(log_file, "Writing data file ##################################", True)
            with open(path_export + "\\{}\\Models\\{}_d.3d".format(package_name, mesh_name), "wb") as Data_File:
                
                log(log_file, "  Writing data file header:", True)
                
                 # prepare export mesh snapshot
                to_exp_bmesh_snap = get_bmesh_snapshot(mesh_obj, flip_model_x, flip_model_y, flip_model_z)
                
                # write header:
                # unsigned short  NumPolygons;  2       - write
                # unsigned short  NumVertices;  2       - write
                # unsigned short  BogusRot;     2       - fill
                # unsigned short  BogusFrame;   2       - fill
                # unsigned long   BogusNormX;   8       - fill
                # unsigned long   BogusNormY;   8       - fill
                # unsigned long   BogusNormZ;   8       - fill
                # unsigned long   FixScale;     8       - fill
                # unsigned long   Unused[3];    3x8     - fill
                # unsigned char   Unknown[12];  12x1    - fill
                # 4xH (unsigned short) 7xL (unsigned long) 12xB (unsigned char)
                Data_File.write(pack("4H7L12B", len(to_exp_bmesh_snap.faces), len(to_exp_bmesh_snap.verts), *([0] * 21)))
                
                log(log_file, "  Writing data file body:", True)

                uv_chan_count = len(to_exp_bmesh_snap.loops.layers.uv)

                # write faces (must be triangles)
                for i, poly in enumerate(to_exp_bmesh_snap.faces):                   
                    
                    if 3 != len(poly.verts):
                        self.report({'ERROR'}, "Error: only triangles are allowed")
                        return {'CANCELLED'}

                    # UVs: get uv coordinates (3) from the face's vertices (also 3)
                    uvs = []
                    for loop in poly.loops:
                        uv = (0, 0)
                        if 0 < uv_chan_count:
                            uv = loop[to_exp_bmesh_snap.loops.layers.uv[0]].uv
                        # restrict to 0-1 range
                        uv[0] = uv[0] % 1
                        uv[1] = uv[1] % 1
                        # flip in u- and v-direction
                        if True == flip_uv_u:
                            uv[0] = 1 - uv[0]
                        if True == flip_uv_v:
                            uv[1] = 1 - uv[1]
                        # scale from 0-1 to 0-255 range
                        uv[0] = uv[0] * 255
                        uv[1] = uv[1] * 255
                        # append to list
                        uvs.append(int(uv[0]))
                        uvs.append(int(uv[1]))

                    # write poly data:
                    # Vertex indices                        - unsigned short [3]
                    face_vert_indices = [vert.index for vert in poly.verts]
                    # James' mesh type                      - char
                    #  DeusEx:
                    #   0  "SKIN" (Normal)
                    #   1  "TWOSIDEDNORM"
                    #   2  "TRANSLUCENT"
                    #   3  "TWOSIDED"
                    #   8  "WEAPON"
                    #   16 "UNLIT"
                    #   32 "FLAT"
                    #   64 "ENVMAPPED"
                    face_type = get_jmesh_type(mesh_obj, poly.material_index)
                    # Color for flat and Gouraud shaded     - char
                    face_color = 0
                    # Texture UV coordinates                - unsigned char [3][2]
                    face_uvs = uvs
                    # Source texture offset                 - char
                    face_mat_idx= poly.material_index + 1
                    # Unreal mesh flags (currently unused)  - char
                    face_flags = 0
                    # 3xH (unsigned short) 2xb (char) 6xB (unsigned char) 2xb (char)
                    print("*face_vert_indices {}, face_type {}, face_color {}, *face_uvs {}, face_mat_idx {}, face_flags {}".format(face_vert_indices, face_type, face_color, face_uvs, face_mat_idx, face_flags))
                    Data_File.write(pack("3H2b6B2b", *face_vert_indices, face_type, face_color, *face_uvs, face_mat_idx, face_flags))

                    log(log_file, "    Polygone {} vertex_indices=({}, {}, {})".format(i, *face_vert_indices))

                # clear snapshot
                clear_bmesh_snapshot(to_exp_bmesh_snap)



            # --------------------------------------------------
            # write unreal class file: .uc
            log(log_file, "Writing class file ##################################", True)
            with open(path_export + "\\{}\\Classes\\{}.uc".format(package_name, class_name), "w") as Class_File:
                Class_File.write("#exec MESH IMPORT MESH={0} ANIVFILE=Models\{0}_a.3d DATAFILE=Models\{0}_d.3d X=0 Y=0 Z=0 unmirror=1\n".format(mesh_name))
                Class_File.write("#exec MESH ORIGIN MESH={} X=0 Y=0 Z=0 ROLL=0\n\n".format(mesh_name))

                Class_File.write("#exec MESH SEQUENCE MESH={} SEQ=All     STARTFRAME=0  NUMFRAMES={}\n".format(mesh_name, frames_exp_count))
                Class_File.write("#exec MESH SEQUENCE MESH={} SEQ=Still  STARTFRAME=0  NUMFRAMES=1\n\n".format(mesh_name))


                Class_File.write("#exec TEXTURE IMPORT NAME=J{0} FILE=Skins\{0}skin.bmp GROUP=\"Skins\"\n\n".format(mesh_name))

                Class_File.write("#exec MESHMAP SCALE MESHMAP={} X=1 Y=1 Z=1\n".format(mesh_name))
                Class_File.write("#exec MESHMAP SETTEXTURE MESHMAP={0} NUM=1 TEXTURE=J{0}\n\n".format(mesh_name))

                Class_File.write("class {} extends Decoration;\n".format(class_name))



            # --------------------------------------------------
            # end of log
            log(log_file, "Export finished", True)

        # --------------------------------------------------
        # end
        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)





def menu_export(self, context):
    self.layout.operator(UnrealMeshExport.bl_idname, text="Unreal1 (_a.3d, _d.3d)")

def register():
    bpy.utils.register_class(UnrealMeshExport)

    bpy.types.TOPBAR_MT_file_export.append(menu_export)

def unregister():
    bpy.utils.unregister_class(UnrealMeshExport)

    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
   
if __name__ == "__main__":
    register()
