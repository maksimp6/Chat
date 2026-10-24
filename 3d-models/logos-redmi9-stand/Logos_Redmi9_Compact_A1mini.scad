// Compact Redmi 9 phone stand, adapted from the provided small STL.
// Depth X=78 mm, width Y=85 mm, height Z=60 mm.
// Center cable opening 27 mm, two retained front lips.
module body() {
    translate([0, 85, 0]) rotate([90, 0, 0])
        linear_extrude(height=85, convexity=10)
            polygon(points=[
                [0,0], [2,0], [2,5], [14,5], [14,0],
                [78,0], [78,60], [15.6,12], [0,12]
            ]);
}
difference() {
    body();
    translate([-1,29,-1]) cube([27,27,32]);
}
