// Подставка для соусов вверх дном: плита + N гнёзд с воронкой. Печать без поддержек.
// Все размеры в мм. Меняйте параметры ниже.

n         = 3;     // число гнёзд
hole_d    = 40;    // диаметр гнезда (внутренний диаметр трубки)
wall      = 2.4;   // толщина стенки трубки и воронки
funnel_d  = 68;    // внутренний диаметр воронки сверху
pitch     = 76.8;  // шаг между гнёздами
margin    = 38.4;  // от центра крайнего гнезда до края плиты
width     = 76.8;  // ширина плиты
corner_r  = 6;     // скругление углов плиты
base_h    = 3;     // толщина плиты
tube_h    = 14;    // высота прямой трубки над плитой
funnel_h  = 26;    // высота воронки
$fn       = 96;

length = (n - 1) * pitch + 2 * margin;

module plate() {
    linear_extrude(base_h)
        offset(r = corner_r) offset(delta = -corner_r)
            square([length, width], center = true);
}

module cup() {
    translate([0, 0, base_h]) {
        difference() {                       // прямая трубка
            cylinder(h = tube_h, d = hole_d + 2 * wall);
            translate([0, 0, -0.01]) cylinder(h = tube_h + 0.02, d = hole_d);
        }
        translate([0, 0, tube_h]) difference() {   // воронка
            cylinder(h = funnel_h, d1 = hole_d + 2 * wall, d2 = funnel_d + 2 * wall);
            translate([0, 0, -0.01])
                cylinder(h = funnel_h + 0.02, d1 = hole_d, d2 = funnel_d);
        }
    }
}

plate();
for (i = [0 : n - 1]) translate([(i - (n - 1) / 2) * pitch, 0, 0]) cup();
