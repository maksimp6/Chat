// Держатель для стандартной пробирки 16 × 100 мм, пробиркой вверх, без поддержек.
// Все размеры в мм.

tube_d   = 16;     // диаметр пробирки
clearance = 0.4;   // зазор на печать (диаметр)
wall     = 2.4;    // стенка
height   = 40;     // высота держателя
base_w   = 40;     // сторона квадратной плиты
base_h   = 3;      // толщина плиты
$fn      = 96;

bore_d = tube_d + clearance;

difference() {
    union() {
        linear_extrude(base_h) offset(r = 4) offset(delta = -4) square(base_w, center = true);
        translate([0, 0, base_h - 0.01]) cylinder(h = height, d = bore_d + 2 * wall);
    }
    translate([0, 0, base_h]) cylinder(h = height + 1, d = bore_d);
}
