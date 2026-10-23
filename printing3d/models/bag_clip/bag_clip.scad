// Зажим с волнистыми губками, шарниром и рычагом-эксцентриком.
// Параметрическая модель по фото (2026-10-11). ВСЕ РАЗМЕРЫ ПРИБЛИЗИТЕЛЬНЫЕ:
// оценены на глаз по фотографии, без замеров оригинала.
//
// Детали (печатаются плашмя, без поддержек):
//   jaw_a  - губка A: верхняя половина шарнира, ось рычага, ловушки под гайки M3
//            (STL уже перевёрнут для печати: диск шарнира на столе)
//   jaw_b  - губка B: нижняя половина шарнира, приподнятый упор под эксцентрик
//   lever  - рычаг: кольцо-эксцентрик вокруг винта M3 и изогнутая ручка
// Крепёж: 2 винта M3 + 2 гайки M3 (или латунные вставки M3, см. fastener).
//   Шарнир губок: винт M3x8, головка утоплена снизу губки B, гайка сверху губки A.
//   Ось рычага:   винт M3x16, головка сверху рычага, гайка снизу губки A.
//
// Рендер одной детали:  openscad -D 'part="lever"' -o lever.stl bag_clip.scad
// part: "plate" (все три детали рядом), "jaw_a", "jaw_b", "lever", "assembly"

part = "plate";

/* [Общие размеры, мм] */
body_t        = 10;    // толщина губок (печатается по Z)
clip_len      = 48;    // расстояние от оси шарнира до центра скругления кончиков
tip_r         = 8;     // радиус скругления кончиков (ширина низа = 2*tip_r + tip_spread)
tip_spread    = 10;    // расстояние между центрами скруглений кончиков
pivot_r       = 9;     // радиус диска шарнира (стенка вокруг винта = pivot_r - hole_d/2)
head_r        = 15;    // радиус «головы» зажима вокруг шарнира

/* [Губки] */
jaw_gap       = 1.0;   // зазор между губками в покое (под ткань/пакет)
wave_amp      = 3;     // амплитуда волны губок
wave_len      = 22;    // длина волны (радиус кривизны ~ wave_len^2/(39.5*wave_amp))
upper_gap     = 3.0;   // зазор между хвостовиками выше шарнира (ограничивает раскрытие)
pivot_clear   = 0.4;   // радиальный зазор шарнира (0.3-0.4 мм)

/* [Рычаг] */
lever_t       = 6;     // толщина рычага
axle_pos      = [-8, 17];  // ось рычага относительно оси шарнира
cam_ecc       = 2;     // эксцентриситет кулачка (ход прижима)
cam_squeeze   = 0.4;   // натяг кулачка на упор в закрытом положении
ring_wall_min = 3;     // минимальная стенка вокруг отверстия рычага (проверяется assert)
arm_len       = 45;    // длина ручки
arm_w         = 8;     // ширина ручки
arm_bend      = 4;     // изгиб ручки (смещение конца вбок)

/* [Крепёж M3] */
fastener      = "nut"; // "nut" - шестигранная гайка M3; "insert" - латунная вставка M3
hole_d        = 3.4;   // сквозное отверстие под винт M3
nut_af        = 5.5;   // гайка M3 под ключ
nut_clear     = 0.3;   // зазор ловушки гайки
nut_h         = 2.6;   // глубина ловушки гайки (гайка 2.4 мм)
insert_d      = 4.0;   // отверстие под термовставку M3 (по паспорту вставки)
insert_h      = 5.0;   // глубина под термовставку
head_d        = 6.0;   // головка винта M3 (DIN 912 5.5 мм + зазор)
head_h        = 3.2;   // глубина утопления головки

$fn = 72;

// ---------- проверки параметров ----------
assert(pivot_r - hole_d/2 >= 3, "Слишком тонкая стенка вокруг оси шарнира");
assert(fastener != "nut" || body_t/2 - nut_h >= 2,
       "Под гайкой в шарнире остаётся меньше 2 мм");
assert(body_t/2 - head_h >= 1.5, "Под головкой винта шарнира остаётся меньше 1.5 мм");

lug_face_x = upper_gap/2;                        // левая грань упора губки B
cam_contact = lug_face_x - axle_pos[0];          // расстояние ось рычага -> упор
// кулачок подобран так, чтобы в закрытом положении давить на упор с натягом cam_squeeze
cam_r_eff = cam_contact + cam_squeeze - cam_ecc;
// радиус кулачка выводится из положения упора, отдельным параметром не задаётся
assert(cam_r_eff - cam_ecc - hole_d/2 >= ring_wall_min,
       str("Стенка вокруг отверстия рычага ", cam_r_eff - cam_ecc - hole_d/2, " мм < ", ring_wall_min));

// ---------- 2D-контуры ----------
module outline2d() {
    hull() {
        circle(r = head_r);
        translate(axle_pos) circle(r = 8);
        translate([-axle_pos[0], axle_pos[1]]) circle(r = 7);
        translate([-tip_spread/2, -clip_len]) circle(r = tip_r);
        translate([ tip_spread/2, -clip_len]) circle(r = tip_r);
    }
}

function wave_x(y, y0) = wave_amp * sin(360 * (y0 - y) / wave_len);

// Полуплоскость слева (side=-1) или справа (side=+1) от линии раздела губок,
// отступив на gap/2. Выше y0 линия прямая (x=0), ниже - волна.
module side2d(side, gap_low, gap_up) {
    big = 200;
    y0 = -(pivot_r + pivot_clear);
    ys = [for (y = [y0 : -0.5 : -clip_len - tip_r - 5]) y];
    pts_low = [for (y = ys) [wave_x(y, y0) + side * gap_low/2, y]];
    // волновая часть
    polygon(concat(
        [[side * big, y0]],
        pts_low,
        [[pts_low[len(pts_low)-1][0], -big], [side * big, -big]]
    ));
    // прямая часть выше начала волны
    translate([side > 0 ? gap_up/2 : -big - gap_up/2, y0 - 0.01]) square([big, big]);
}

module region2d(side) {
    intersection() { outline2d(); side2d(side, jaw_gap, upper_gap); }
}

// ---------- крепёж ----------
module nut_trap() { cylinder(d = (nut_af + nut_clear) / cos(30), h = nut_h, $fn = 6); }

module bottom_fastener() {           // со стороны дна детали (z = 0)
    translate([0, 0, -0.01])
    if (fastener == "nut") nut_trap();
    else cylinder(d = insert_d, h = insert_h);
}

module through_hole(h) { translate([0, 0, -1]) cylinder(d = hole_d, h = h + 2); }

// ---------- губка A ----------
// Нижний слой: своя область без диска шарнира; верхний слой: своя область + диск.
// Печатается перевёрнутой (диск шарнира ложится на стол), см. jaw_a_print().
module jaw_a() {
    difference() {
        union() {
            linear_extrude(body_t/2)
                difference() { region2d(-1); circle(r = pivot_r + pivot_clear); }
            translate([0, 0, body_t/2]) linear_extrude(body_t/2)
                union() { region2d(-1); circle(r = pivot_r); }
        }
        // шарнир: гайка/вставка сверху диска
        through_hole(body_t);
        translate([0, 0, body_t]) mirror([0, 0, 1]) bottom_fastener();
        // ось рычага: гайка/вставка снизу
        translate(axle_pos) { through_hole(body_t); bottom_fastener(); }
    }
}

module jaw_a_print() { translate([0, 0, body_t]) rotate([0, 180, 0]) jaw_a(); }

// ---------- губка B ----------
// Нижний слой: своя область + диск шарнира; верхний слой: без диска.
// Печатается как есть, упор под кулачок растёт вверх.
module jaw_b() {
    difference() {
        union() {
            linear_extrude(body_t/2)
                union() { region2d(1); circle(r = pivot_r); }
            translate([0, 0, body_t/2]) linear_extrude(body_t/2)
                difference() { region2d(1); circle(r = pivot_r + pivot_clear); }
            // упор под кулачок: хвостовик B поднят до верха рычага
            linear_extrude(body_t + lever_t)
                intersection() {
                    region2d(1);
                    translate([0, pivot_r + pivot_clear + 1]) square([50, 50]);
                }
        }
        through_hole(body_t);
        // головка винта шарнира утоплена снизу
        translate([0, 0, -1]) cylinder(d = head_d, h = head_h + 1);
    }
}

// ---------- рычаг ----------
// Локальные координаты: центр отверстия в начале, кулачок смещён по +X
// (в закрытом положении смотрит на упор губки B), ручка уходит вниз (-Y).
module lever2d() {
    // кольцо-эксцентрик
    translate([cam_ecc, 0]) circle(r = cam_r_eff);
    // изогнутая ручка (дуга из кругов)
    n = 12;
    for (i = [0 : n - 1]) hull() {
        for (j = [i, i + 1]) {
            t = j / n;
            translate([-arm_bend * sin(180 * t) - 2 * t, -arm_len * t])
                circle(d = arm_w * (1 - 0.15 * t));
        }
    }
}

module lever() {
    difference() {
        linear_extrude(lever_t) lever2d();
        through_hole(lever_t);
        // головка винта оси рычага утоплена сверху
        translate([0, 0, lever_t - min(head_h, lever_t - 3)])
            cylinder(d = head_d, h = head_h + 1);
    }
}

// ---------- компоновка ----------
// Рабочее положение: губка B снизу (её диск в нижнем слое), губка A сверху.
module assembly() {
    color("silver") jaw_a();
    color("lightgray") jaw_b();
    color("dimgray") translate([axle_pos[0], axle_pos[1], body_t]) lever();
}

module plate() {
    // три детали рядом на столе A1 mini (180x180)
    translate([-25, 20, 0]) jaw_a_print();
    translate([ 15, 20, 0]) jaw_b();
    translate([ 55, 35, 0]) lever();
}

if (part == "jaw_a") jaw_a_print();
else if (part == "jaw_b") jaw_b();
else if (part == "lever") lever();
else if (part == "assembly") assembly();
else plate();

echo(str("Стенка вокруг отверстия рычага, мин: ", cam_r_eff - cam_ecc - hole_d/2, " мм"));
tip_ratio = (clip_len + tip_r) / axle_pos[1];
echo(str("Прижим на кончике губок (закрыто) ~ ", cam_squeeze * tip_ratio - jaw_gap, " мм натяга"));
echo(str("Раскрытие кончиков ~ ", jaw_gap + min(2 * cam_ecc - cam_squeeze, upper_gap * axle_pos[1] / (axle_pos[1] + 8)) * tip_ratio, " мм"));
