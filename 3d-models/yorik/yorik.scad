// Yorik: stylized seated Yorkshire terrier, single-piece model.
$fn=36;
module oval(p,s){translate(p) scale(s) sphere(1);}
module ear(side){hull(){oval([side*8,0,42],[4.3,3,2]);oval([side*11,0,52],[1.3,1.5,1.7]);oval([side*4,-1,44],[2,2.5,2]);}}
difference(){
union(){
// Flat stable pedestal and seated body
cylinder(h=2.4,r=17,$fn=80);
oval([0,2,16],[9,8,14]);
oval([-8,2,8],[6,7,6]); oval([8,2,8],[6,7,6]);
// Forelegs and toes blended into base
for(s=[-1,1]){
hull(){oval([s*4.8,-4,18],[3.3,3.2,4]);oval([s*4.8,-7,5],[3.6,4,3.1]);}
oval([s*5,-8,4],[4.2,4.8,2.4]);
}
// Neck, broad head, cheeks and muzzle
oval([0,0,29],[7,6,8]);
oval([0,-1,39],[11,8,10]);
for(s=[-1,1]){oval([s*6.8,-5,34],[5,4.5,6]); ear(s);}
oval([0,-7,35],[6,4,4]);
oval([0,-10,37],[2.4,1.8,1.6]);
// Brow, forehead tuft and long beard
for(s=[-1,1]) oval([s*4.6,-7,41],[4,2,2]);
hull(){oval([0,-7,32],[4.5,2.8,2.5]);oval([0,-5,28],[2,2,2]);}
for(s=[-1,1]) for(i=[0:2]) hull(){oval([s*(6+i),-3,36],[2,2,3]);oval([s*(5+i),-2,29+i],[1.3,1.8,2]);}
oval([0,-1,47],[3,4,3]);
// Tail attached along body for strength
hull(){oval([8,7,11],[3,3,4]);oval([11,6,19],[2.4,2.4,3]);}
}
// Recessed eye sockets and ear details
for(s=[-1,1]){
oval([s*4.5,-8.7,39.5],[1.35,1.6,1.35]);
hull(){oval([s*8,-2.1,44],[1.5,1.4,1]);oval([s*10,-1,49],[0.6,1,0.7]);}
// Paw clefts
for(x=[-0.9,0.9]) translate([s*5+x,-12,3.8]) cube([0.6,2,1.2],center=true);
}
// Subtle mouth line
oval([0,-10.65,33.7],[2.3,0.8,0.5]);
}
