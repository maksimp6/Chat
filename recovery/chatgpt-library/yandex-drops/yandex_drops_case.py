#!/usr/bin/env python3
"""Parametric, dimension-UNVERIFIED, 2-piece Yandex Drops TPU case.

Units: mm. Coordinate frame of the reference photo:
  X: width, +X to right/carabinier loop; Y: height, +Y toward lid top;
  Z: depth, +Z toward visible/front face, -Z toward rear hinge.

Generates two distinct solids:
  - lid cap, open toward negative Y
  - base sleeve, open toward positive Y

Never accesses slicers, printers, networks or storage outside --out.
Front spot in reference photo is NOT verified to be an LED.
Charging port position and hinge relief are placeholders, not product measurements.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import cadquery as cq
from cadquery import exporters
import trimesh


@dataclass(frozen=True)
class Parameters:
    width: float
    height: float
    depth: float
    corner_r: float
    depth_r: float
    lid_fraction: float
    wall: float
    clearance: float
    separation: float
    hinge_w: float
    hinge_h: float
    hinge_d: float
    led_enabled: bool
    led_x: float
    led_y: float
    led_d: float
    usb_enabled: bool
    usb_x: float
    usb_z: float
    usb_w: float
    usb_h: float
    usb_r: float
    loop_enabled: bool
    loop_od: float
    loop_hole: float
    loop_t: float
    loop_projection: float
    loop_y_offset: float

    @property
    def seam_y(self) -> float:
        return self.height / 2 - self.height * self.lid_fraction

    @property
    def outer_width(self) -> float:
        return self.width + 2 * (self.wall + self.clearance)

    @property
    def outer_height(self) -> float:
        return self.height + 2 * (self.wall + self.clearance)

    @property
    def outer_depth(self) -> float:
        return self.depth + 2 * (self.wall + self.clearance)

    @property
    def loop_xy(self) -> tuple[float, float]:
        # Lower-body side loop is flat on the build plate after orientation.
        return (
            self.outer_width / 2 + self.loop_projection,
            -self.outer_height / 2 + self.loop_t / 2 + self.loop_y_offset,
        )


def from_config(data: dict[str, Any]) -> Parameters:
    m, t, h = data["measurements"], data["tpu_fit"], data["hinge"]
    led, usb, loop = data["led"], data["charging_port"], data["lanyard_loop"]
    p = Parameters(
        width=float(m["case_width_mm"]),
        height=float(m["case_height_mm"]),
        depth=float(m["case_depth_mm"]),
        corner_r=float(m["case_xy_corner_radius_mm"]),
        depth_r=float(m["case_depth_edge_radius_mm"]),
        lid_fraction=float(m["lid_fraction_from_top"]),
        wall=float(t["wall_mm"]),
        clearance=float(t["clearance_each_side_mm"]),
        separation=float(t["lid_base_separation_mm"]),
        hinge_w=float(h["relief_width_mm"]),
        hinge_h=float(h["relief_height_mm"]),
        hinge_d=float(h["relief_depth_mm"]),
        led_enabled=bool(led["enabled"]),
        led_x=float(led["front_x_mm"]),
        led_y=float(led["front_y_mm"]),
        led_d=float(led["hole_diameter_mm"]),
        usb_enabled=bool(usb["enabled"]),
        usb_x=float(usb["bottom_x_mm"]),
        usb_z=float(usb["depth_z_mm"]),
        usb_w=float(usb["opening_width_mm"]),
        usb_h=float(usb["opening_depth_mm"]),
        usb_r=float(usb["opening_radius_mm"]),
        loop_enabled=bool(loop["enabled"]),
        loop_od=float(loop["outer_diameter_mm"]),
        loop_hole=float(loop["hole_diameter_mm"]),
        loop_t=float(loop["print_thickness_mm"]),
        loop_projection=float(loop["center_projection_mm"]),
        loop_y_offset=float(loop["base_y_offset_mm"]),
    )
    validate_params(p)
    return p


def validate_params(p: Parameters) -> None:
    if not 35 <= p.width <= 100 or not 25 <= p.height <= 80 or not 12 <= p.depth <= 50:
        raise ValueError("Case body dimensions outside supported CAD design envelope")
    if not 3 <= p.corner_r <= min(p.width, p.height) / 2 - 1:
        raise ValueError("Invalid XY corner radius")
    if not 1 <= p.depth_r < min(p.depth / 2, p.corner_r):
        raise ValueError("Invalid thickness-edge radius")
    if not 0.52 <= p.lid_fraction <= 0.80:
        raise ValueError("Unreasonable lid/hinge split fraction")
    if not 1.2 <= p.wall <= 3.0 or not 0.05 <= p.clearance <= 1.2:
        raise ValueError("Wall or clearance outside bounded prototype range")
    if not 0.3 <= p.separation <= 2.0:
        raise ValueError("Invalid cap/base clearance")
    if p.hinge_w <= 4 or p.hinge_w >= p.width - 8:
        raise ValueError("Hinge notch width must leave rear wall supports")
    if p.hinge_h <= p.separation or p.hinge_h > p.height * 0.48:
        raise ValueError("Hinge notch height exceeds case seam envelope")
    if not p.wall <= p.hinge_d <= p.depth * 0.48:
        raise ValueError("Hinge notch depth outside supported range")
    top_seam = p.seam_y + p.separation / 2
    if p.led_enabled and not (
        1.6 <= p.led_d <= 7
        and abs(p.led_x) <= p.width / 2 - p.corner_r - 2
        and top_seam + p.led_d / 2 + 2 < p.led_y < p.height / 2 - p.led_d / 2 - 3
    ):
        raise ValueError("LED aperture must fit on front face of upper cap")
    if p.usb_enabled and not (
        5 <= p.usb_w <= p.width * 0.50
        and 2 <= p.usb_h <= p.depth * 0.5
        and 0.5 <= p.usb_r < min(p.usb_w, p.usb_h) / 2
        and abs(p.usb_x) < p.width / 2 - p.usb_w / 2 - p.corner_r / 2
        and abs(p.usb_z) < p.depth / 2 - p.usb_h / 2 - p.depth_r / 2
    ):
        raise ValueError("USB cutout lies outside valid lower shell end region")
    if p.loop_enabled and not (
        9 <= p.loop_od <= 20
        and 3 <= p.loop_hole <= p.loop_od - 4.5
        and 3 <= p.loop_t <= min(p.depth - 4, (p.seam_y - p.separation / 2 + p.outer_height / 2) - 2)
        and p.loop_od / 2 - 3 <= p.loop_projection <= p.loop_od / 2 - 0.8
        and 0 <= p.loop_y_offset <= 0.5
    ):
        raise ValueError("Lanyard loop would protrude through seam/bottom or disconnect")


def rounded_body(width: float, height: float, depth: float, xy_radius: float, z_radius: float) -> cq.Workplane:
    """Smooth rounded cuboid: constant radius in photo plane, rolled front/back edges."""
    return (
        cq.Workplane("XY")
        .box(width, height, depth)
        .edges("|Z")
        .fillet(xy_radius)
        .edges("not |Z")
        .fillet(z_radius)
    )


def create_assembled_solids(p: Parameters) -> dict[str, cq.Workplane]:
    # Continuous shell ensures interior references the whole physical charging case.
    inner = rounded_body(
        p.width + 2 * p.clearance,
        p.height + 2 * p.clearance,
        p.depth + 2 * p.clearance,
        p.corner_r + p.clearance,
        p.depth_r + p.clearance,
    )
    outer = rounded_body(
        p.outer_width,
        p.outer_height,
        p.outer_depth,
        p.corner_r + p.clearance + p.wall,
        p.depth_r + p.clearance + p.wall,
    )
    empty_shell = outer.cut(inner)
    # Cut entire shell at photo-derived seam so both pieces are truly independent.
    top_cut = cq.Workplane("XY").box(200, 200, 120).translate((0, p.seam_y + p.separation / 2 + 100, 0))
    base_cut = cq.Workplane("XY").box(200, 200, 120).translate((0, p.seam_y - p.separation / 2 - 100, 0))
    lid = empty_shell.intersect(top_cut)
    base = empty_shell.intersect(base_cut)

    # Rear hinge may need greater clearance than this provisional slot.
    hinge = cq.Workplane("XY").box(p.hinge_w, p.hinge_h, p.hinge_d + 0.8).translate(
        (0, p.seam_y, -p.outer_depth / 2 + (p.hinge_d + 0.8) / 2 - 0.6)
    )
    lid = lid.cut(hinge)
    base = base.cut(hinge)

    if p.led_enabled:
        led_cutter = (
            cq.Workplane("XY")
            .center(p.led_x, p.led_y)
            .circle(p.led_d / 2)
            .extrude(p.wall + 3)
            .translate((0, 0, (p.depth + 2 * p.clearance) / 2 - 1))
        )
        lid = lid.cut(led_cutter)

    if p.usb_enabled:
        # Rounded rectangular port, penetrating the bottom (-Y) end face.
        cut_thickness = p.wall + 8
        usb_cutter = (
            cq.Workplane("XY")
            .box(p.usb_w, cut_thickness, p.usb_h)
            .edges("|Y")
            .fillet(p.usb_r)
            .translate((p.usb_x, -p.outer_height / 2 - 1 + cut_thickness / 2, p.usb_z))
        )
        base = base.cut(usb_cutter)

    if p.loop_enabled:
        cx, cy = p.loop_xy
        bottom_y = -p.outer_height / 2 + p.loop_y_offset
        # The ring is in the original XZ plane and traverses Y: after printing
        # orientation it lies flat on the build plate, avoiding a horizontal
        # floating ear that would otherwise require TPU support structures.
        lanyard = (
            cq.Workplane("XZ")
            .center(cx, 0)
            .circle(p.loop_od / 2)
            .circle(p.loop_hole / 2)
            .extrude(p.loop_t)
            .translate((0, bottom_y + p.loop_t, 0))
        )
        # A wide and continuous rooted neck integrates the annulus into the
        # rounded body; all interior intrusions are removed by the cavity cut.
        root_x = p.outer_width / 2 - p.corner_r * 0.85
        neck_far_x = cx + p.loop_hole / 2 + 0.7
        neck = (cq.Workplane("XY")
                .box(neck_far_x - root_x, p.loop_t, p.loop_hole + 3.5)
                .translate(((neck_far_x + root_x) / 2, cy, 0)))
        loop_hole_cutter = (cq.Workplane("XZ").center(cx, 0)
                             .circle(p.loop_hole / 2).extrude(p.loop_t + 2)
                             .translate((0, bottom_y + p.loop_t + 1, 0)))
        base = base.union(neck).union(lanyard).cut(inner).cut(loop_hole_cutter)
        base = base.intersect(base_cut)

    for name, shape in {"lid": lid, "base": base}.items():
        solid = shape.val()
        if not solid.isValid() or len(shape.solids().vals()) != 1:
            raise ValueError(f"{name} CAD is not a single valid connected solid")
    return {"lid": lid, "base": base}


def print_oriented(shape: cq.Workplane, part: str) -> cq.Workplane:
    """Orient closed cap end against print bed, openings face upward."""
    angle = -90 if part == "lid" else 90
    rotated = shape.rotate((0, 0, 0), (1, 0, 0), angle)
    bb = rotated.val().BoundingBox()
    return rotated.translate((-(bb.xmin + bb.xmax) / 2, -(bb.ymin + bb.ymax) / 2, -bb.zmin))


def mesh_validation(stl: Path) -> dict[str, Any]:
    mesh = trimesh.load(stl, force="mesh", process=True)
    # STL stores triangle soup: explicit merge vertices before watertight analysis.
    mesh.merge_vertices(digits_vertex=5)
    dims = mesh.extents
    layer_vertices = mesh.vertices[mesh.faces][:, :, 2]
    contact_faces = layer_vertices.max(axis=1) <= 0.03
    contact_area = float(mesh.area_faces[contact_faces].sum())
    report = {
        "filename": stl.name,
        "sha256": hashlib.sha256(stl.read_bytes()).hexdigest(),
        "triangles": int(len(mesh.faces)),
        "vertices_after_welding": int(len(mesh.vertices)),
        "bounds_mm": [round(float(n), 3) for n in dims],
        "min_print_z_mm": round(float(mesh.bounds[0][2]), 5),
        "flat_bed_contact_area_mm2": round(contact_area, 2),
        "watertight": bool(mesh.is_watertight),
        "winding_consistent": bool(mesh.is_winding_consistent),
        "connected_bodies": len(mesh.split(only_watertight=False)),
        "volume_mm3": round(float(mesh.volume), 3),
        "fits_A1_mini_180mm": bool(all(0 < v <= 180 for v in dims)),
    }
    if not (report["watertight"] and report["winding_consistent"] and report["connected_bodies"] == 1
            and report["volume_mm3"] > 50 and report["fits_A1_mini_180mm"]
            and abs(report["min_print_z_mm"]) < 0.03
            and report["flat_bed_contact_area_mm2"] >= 150):
        raise ValueError(f"Unusable STL mesh: {report}")
    return report


def generate(config: dict[str, Any], outdir: Path) -> dict[str, Any]:
    p = from_config(config)
    parts = create_assembled_solids(p)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "stl").mkdir(exist_ok=True)
    (outdir / "step").mkdir(exist_ok=True)
    validations: dict[str, Any] = {}
    for part, cad in parts.items():
        stl = outdir / "stl" / f"Yandex_Drops_TPU_{part}_PRINT_ORIENTED_UNVERIFIED.stl"
        step = outdir / "step" / f"Yandex_Drops_TPU_{part}_ASSEMBLY_COORDS.step"
        exporters.export(print_oriented(cad, part), str(stl), tolerance=0.09, angularTolerance=0.13)
        exporters.export(cad, str(step))
        validations[part] = mesh_validation(stl)
        validations[part]["step_bytes"] = step.stat().st_size
    summary = {
        "status": "CAD_AND_MESH_VERIFIED__NOT_PHYSICALLY_VERIFIED",
        "print_start_authorized": False,
        "no_gcode_created": True,
        "no_printer_contact": True,
        "fit_dimensions_verified": False,
        "image_photo_based": True,
        "geometry": {
            "seam_y_mm": round(p.seam_y, 3),
            "outer_size_mm_excluding_loop": [round(p.outer_width, 2),round(p.outer_height, 2),round(p.outer_depth, 2)],
            "inner_size_mm": [p.width+2*p.clearance,p.height+2*p.clearance,p.depth+2*p.clearance],
            "loop_center_xy_mm": [round(n, 3) for n in p.loop_xy],
            "lid_fraction_from_top": p.lid_fraction,
        },
        "sources": "Editable CadQuery parametric source + estimated JSON; source photo supplied by user",
        "stl_files": validations,
    }
    (outdir / "reports").mkdir(exist_ok=True)
    (outdir / "reports" / "geometry_validation.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, required=True, help="JSON dimensions (all assumptions adjustable)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    print(json.dumps(generate(cfg, args.out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()