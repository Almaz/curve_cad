#  ***** GPL LICENSE BLOCK *****
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <http://www.gnu.org/licenses/>.
#  All rights reserved.
#
#  ***** GPL LICENSE BLOCK *****

import bpy, math, bmesh
from mathutils import Vector, Matrix
from . import internal

class OffsetCurve(bpy.types.Operator):
    bl_idname = 'curve.add_toolpath_offset_curve'
    bl_description = bl_label = 'Offset Curve'
    bl_options = {'REGISTER', 'UNDO'}

    offset: bpy.props.FloatProperty(name='Offset', description='Distace between the original and the first trace', unit='LENGTH', default=0.1)
    pitch: bpy.props.FloatProperty(name='Pitch', description='Distace between two parallel traces', unit='LENGTH', default=0.1)
    step_angle: bpy.props.FloatProperty(name='Resolution', description='Smaller values make curves smoother by adding more vertices', unit='ROTATION', min=math.pi/128, default=math.pi/16)
    count: bpy.props.IntProperty(name='Count', description='Number of parallel traces', min=1, default=1)
    round_line_join: bpy.props.BoolProperty(name='Round Line Join', description='Insert circle arcs at convex corners', default=True)

    @classmethod
    def poll(cls, context):
        return bpy.context.object != None and bpy.context.object.type == 'CURVE'

    def execute(self, context):
        if bpy.context.object.mode == 'EDIT':
            splines = internal.getSelectedSplines(True, True)
        else:
            splines = bpy.context.object.data.splines

        if len(splines) == 0:
            self.report({'WARNING'}, 'Nothing selected')
            return {'CANCELLED'}

        if bpy.context.object.mode != 'EDIT':
            internal.addObject('CURVE', 'Offset Toolpath')
            origin = bpy.context.scene.cursor.location
        else:
            origin = Vector((0.0, 0.0, 0.0))

        for spline in splines:
            spline_points = spline.bezier_points if spline.type == 'BEZIER' else spline.points
            for spline_point in spline_points:
                if spline_point.co.z != spline_points[0].co.z:
                    self.report({'WARNING'}, 'Curves must be planar and in XY plane')
                    return {'CANCELLED'}
            for index in range(0, self.count):
                traces = internal.offsetPolygonOfSpline(spline, self.offset+self.pitch*index, self.step_angle, self.round_line_join)
                for trace in traces:
                    internal.addPolygonSpline(bpy.context.object, spline.use_cyclic_u, [vertex-origin for vertex in trace])
        return {'FINISHED'}

class SliceMesh(bpy.types.Operator):
    bl_idname = 'curve.add_toolpath_slice_mesh'
    bl_description = bl_label = 'Slice Mesh'
    bl_options = {'REGISTER', 'UNDO'}

    pitch_axis: bpy.props.FloatVectorProperty(name='Pitch & Axis', unit='LENGTH', description='Vector between to slices', subtype='DIRECTION', default=(0.0, 0.0, 0.1), size=3)
    offset: bpy.props.FloatProperty(name='Offset', unit='LENGTH', description='Position of first slice along axis', default=-0.4)
    slice_count: bpy.props.IntProperty(name='Count', description='Number of slices', min=1, default=9)

    @classmethod
    def poll(cls, context):
        return bpy.context.object != None and bpy.context.object.mode == 'OBJECT'

    def execute(self, context):
        if bpy.context.object.type != 'MESH':
            self.report({'WARNING'}, 'Active object must be a mesh')
            return {'CANCELLED'}
        depsgraph = context.evaluated_depsgraph_get()
        mesh = bmesh.new()
        mesh.from_object(bpy.context.object, depsgraph, deform=True, cage=False, face_normals=True)
        mesh.transform(bpy.context.object.matrix_world)
        toolpath = internal.addObject('CURVE', 'Slices Toolpath')
        pitch_axis = Vector(self.pitch_axis)
        axis = pitch_axis.normalized()
        for i in range(0, self.slice_count):
            aux_mesh = mesh.copy()
            cut_geometry = bmesh.ops.bisect_plane(aux_mesh, geom=aux_mesh.edges[:]+aux_mesh.faces[:], dist=0, plane_co=pitch_axis*i+axis*self.offset, plane_no=axis, clear_outer=False, clear_inner=False)['geom_cut']
            edge_pool = set([e for e in cut_geometry if isinstance(e, bmesh.types.BMEdge)])
            while len(edge_pool) > 0:
                current_edge = edge_pool.pop()
                first_vertex = current_vertex = current_edge.verts[0]
                vertices = [current_vertex.co]
                follow_edge_loop = len(edge_pool) > 0
                while follow_edge_loop:
                    current_vertex = current_edge.other_vert(current_vertex)
                    vertices.append(current_vertex.co)
                    if current_vertex == first_vertex:
                        break
                    follow_edge_loop = False
                    for edge in current_vertex.link_edges:
                        if edge in edge_pool:
                            current_edge = edge
                            edge_pool.remove(current_edge)
                            follow_edge_loop = True
                            break
                current_vertex = current_edge.other_vert(current_vertex)
                vertices.append(current_vertex.co)
                internal.addPolygonSpline(toolpath, False, vertices)
            aux_mesh.free()
        mesh.free()
        return {'FINISHED'}

class Truncate(bpy.types.Operator):
    bl_idname = 'curve.add_toolpath_truncate'
    bl_description = bl_label = 'Truncate'
    bl_options = {'REGISTER', 'UNDO'}

    min_dist: bpy.props.FloatProperty(name='Min Distance', unit='LENGTH', description='Remove vertices which are too close together', min=0.0, default=0.001)
    z_hop: bpy.props.BoolProperty(name='Z Hop', description='Add movements to the ceiling at trace ends', default=True)

    @classmethod
    def poll(cls, context):
        return bpy.context.object != None and bpy.context.object.mode == 'OBJECT'

    def execute(self, context):
        if bpy.context.object.type != 'EMPTY' or bpy.context.object.empty_display_type != 'CUBE':
            self.report({'WARNING'}, 'Active object must be an empty of display type cube')
            return {'CANCELLED'}
        selection = bpy.context.selected_objects[:]
        workspace = bpy.context.object
        aabb = internal.AABB(center=Vector((0.0, 0.0, 0.0)), dimensions=Vector((1.0, 1.0, 1.0))*workspace.empty_display_size)
        toolpath = internal.addObject('CURVE', 'Truncated Toolpath')
        for curve in selection:
            if curve.type == 'CURVE':
                transform = workspace.matrix_world.inverted()@curve.matrix_world
                inverse_transform = Matrix.Translation(-toolpath.location)@workspace.matrix_world
                curve_traces = []
                for spline in curve.data.splines:
                    if spline.type == 'POLY':
                        curve_traces += internal.truncateToFitBox(transform, spline, aabb)
                for trace in curve_traces:
                    i = len(trace[0])-1
                    while i > 1:
                        if (trace[0][i-1]-trace[0][i]).length < self.min_dist:
                            trace[0].pop(i-1)
                            trace[1].pop(i-1)
                        i -= 1
                    if self.z_hop:
                        begin = Vector(trace[0][0])
                        end = Vector(trace[0][-1])
                        begin.z = end.z = workspace.empty_display_size
                        trace[0].insert(0, begin)
                        trace[1].insert(0, 1.0)
                        trace[0].append(end)
                        trace[1].append(1.0)
                    internal.addPolygonSpline(toolpath, False, [inverse_transform@vertex for vertex in trace[0]], trace[1])
        return {'FINISHED'}

class RectMacro(bpy.types.Operator):
    bl_idname = 'curve.add_toolpath_rect_macro'
    bl_description = bl_label = 'Rect Macro'
    bl_options = {'REGISTER', 'UNDO'}

    track_count: bpy.props.IntProperty(name='Number Tracks', description='How many tracks', min=1, default=10)
    stride: bpy.props.FloatProperty(name='Stride', unit='LENGTH', description='Distance to previous track on the way back', min=0.0, default=0.5)
    pitch: bpy.props.FloatProperty(name='Pitch', unit='LENGTH', description='Distance between two tracks', default=-1.0)
    length: bpy.props.FloatProperty(name='Length', unit='LENGTH', description='Length of one track', default=10.0)
    speed: bpy.props.FloatProperty(name='Speed', description='Stored in softbody goal weight', min=0.0, max=1.0, default=0.1)

    def execute(self, context):
        if not internal.curveObject():
            internal.addObject('CURVE', 'Rect Toolpath')
            origin = Vector((0.0, 0.0, 0.0))
        else:
            origin = bpy.context.scene.cursor.location
        stride = math.copysign(self.stride, self.pitch)
        length = self.length*0.5
        vertices = []
        weights = []
        for i in range(0, self.track_count):
            shift = i*self.pitch
            flipped = -1 if (stride == 0 and i%2 == 1) else 1
            vertices.append(origin+Vector((shift, -length*flipped, 0.0)))
            weights.append(self.speed)
            vertices.append(origin+Vector((shift, length*flipped, 0.0)))
            weights.append(self.speed)
            if stride != 0:
                vertices.append(origin+Vector((shift-stride, length, 0.0)))
                weights.append(self.speed)
                vertices.append(origin+Vector((shift-stride, -length, 0.0)))
                weights.append(1)
        internal.addPolygonSpline(bpy.context.object, False, vertices, weights)
        return {'FINISHED'}

class DrillMacro(bpy.types.Operator):
    bl_idname = 'curve.add_toolpath_drill_macro'
    bl_description = bl_label = 'Drill Macro'
    bl_options = {'REGISTER', 'UNDO'}

    screw_count: bpy.props.FloatProperty(name='Screw Turns', description='How many screw truns', min=1.0, default=10.0)
    spiral_count: bpy.props.FloatProperty(name='Spiral Turns', description='How many spiral turns', min=0.0, default=0.0)
    vertex_count: bpy.props.IntProperty(name='Number Vertices', description = 'How many vertices per screw turn', min=3, default=32)
    radius: bpy.props.FloatProperty(name='Radius', unit='LENGTH', description='Radius at tool center', min=0.0, default=5.0)
    pitch: bpy.props.FloatProperty(name='Pitch', unit='LENGTH', description='Distance between two screw turns', min=0.0, default=1.0)
    speed: bpy.props.FloatProperty(name='Speed', description='Stored in softbody goal weight', min=0.0, max=1.0, default=0.1)

    def execute(self, context):
        if not internal.curveObject():
            internal.addObject('CURVE', 'Drill Toolpath')
            origin = Vector((0.0, 0.0, 0.0))
        else:
            origin = bpy.context.scene.cursor.location
        count = int(self.vertex_count*self.screw_count)
        height = -count/self.vertex_count*self.pitch
        vertices = []
        weights = []
        def addRadialVertex(param, radius, height):
            angle = param*math.pi*2
            vertices.append(origin+Vector((math.sin(angle)*radius, math.cos(angle)*radius, height)))
            weights.append(self.speed)
        if self.radius > 0:
            if self.spiral_count > 0.0:
                sCount = math.ceil(self.spiral_count*self.vertex_count)
                for j in range(1, int(self.screw_count)+1):
                    if j > 1:
                        vertices.append(origin+Vector((0.0, 0.0, sHeight)))
                        weights.append(self.speed)
                    sHeight = max(-j*self.pitch, height)
                    for i in range(0, sCount+1):
                        sParam = i/self.vertex_count
                        addRadialVertex(sParam, i/sCount*self.radius, sHeight)
                    for i in range(0, self.vertex_count+1):
                        addRadialVertex(sParam+(count+i)/self.vertex_count, self.radius, sHeight)
            else:
                for i in range(0, count):
                    param = i/self.vertex_count
                    addRadialVertex(param, self.radius, -param*self.pitch)
                for i in range(0, self.vertex_count+1):
                    addRadialVertex((count+i)/self.vertex_count, self.radius, height)
            weights += [1, 1]
        else:
            weights += [self.speed, 1]
        vertices += [origin+Vector((0.0, 0.0, height)), origin]
        internal.addPolygonSpline(bpy.context.object, False, vertices, weights)
        return {'FINISHED'}

operators = [OffsetCurve, SliceMesh, Truncate, RectMacro, DrillMacro]
