// Parametric Phone Stand - prints on its side, no supports needed
// Change the values below to customize.

width        = 70;   // stand width (mm)
thick        = 4;    // wall thickness (mm)
angle        = 65;   // backrest angle from horizontal (deg)
back_len     = 95;   // backrest length (mm)
pocket       = 13;   // gap for phone + case (mm)
lip_h        = 14;   // front lip height (mm)
base_len     = 85;   // base depth (mm)
cable_w      = 14;   // charging-cable slot width (0 = none)
round_r      = 1.5;  // edge rounding (mm)

$fn = 48;

bx = thick + pocket;                     // backrest foot x
tx = bx + back_len * cos(angle);         // backrest top x
ty = back_len * sin(angle);              // backrest top y

module rounded() { offset(r=round_r) offset(delta=-round_r) children(); }

module frame() {
    // outer triangle minus inner triangle = rigid truss
    difference() {
        polygon([[bx,0],[tx,ty],[tx+thick*0.6,ty-thick*0.4],[base_len,0]]);
        offset(delta=-thick) polygon([[bx,0],[tx,ty],[base_len,0]]);
    }
}

module profile() {
    rounded() union() {
        square([base_len, thick]);           // base
        square([thick, lip_h]);              // front lip
        frame();                             // backrest + strut
    }
}

module stand() {
    difference() {
        linear_extrude(width) profile();
        // cable slot through lip and base front
        if (cable_w > 0)
            translate([-1, -1, width/2 - cable_w/2])
                cube([bx + 2, lip_h + 2, cable_w]);
    }
}

stand();
