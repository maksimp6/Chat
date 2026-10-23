// Logos Redmi 9 Stand v3: one-piece desktop holder for Bambu A1 mini.
// Redmi 9 nominal: 163.32 x 77.01 x 9.1 mm (without case).
// Base flat on bed; X width, Y points backwards, Z up.
module yz_prism(profile,width,xstart) {
    translate([xstart,0,0])
    multmatrix([[0,0,1,0],[1,0,0,0],[0,1,0,0],[0,0,0,1]])
    linear_extrude(height=width) polygon(points=profile);
}
union() {
    translate([-56,0,0]) cube([112,104,6]);
    yz_prism([[33,5],[40,5],[73.1,107],[66.1,107]],86,-43);
    for (x=[-42,34])
        yz_prism([[39,5],[98,5],[73,107],[65,98]],8,x);
    for (x=[-49,15]) {
        translate([x,13,6]) cube([34,34,8]);
        translate([x,13,14]) cube([34,7,14]);
        yz_prism([[13,6],[47,6],[42,13],[20,13]],34,x);
    }
    translate([0,6.5,6]) linear_extrude(height=1)
        text("LOGOS",size=9,font="DejaVu Sans:style=Bold",halign="center",valign="center");
}
