package org.alice.pet;
import android.app.*;
import android.os.*;
import android.content.*;
import android.graphics.*;
import android.view.*;
import android.util.Log;
import android.util.DisplayMetrics;

public class PetActivity extends Activity {
  @Override public void onCreate(Bundle b) {
    super.onCreate(b);
    Intent command=new Intent(this, PetService.class);
    if(getIntent()!=null && getIntent().getExtras()!=null) command.putExtras(getIntent().getExtras());
    startService(command);
    finish();
  }
  public static class PetView extends View {
    static { try { System.loadLibrary("alicepetvulkan"); } catch(Throwable t) { Log.e("AlicePetVulkan","load failed",t); } }
    public native String probeVulkanSurface(android.view.Surface surface);
    Paint p=new Paint(3);
    boolean ember=false;
    void setEmber(boolean enabled){ember=enabled;invalidate();}
    public PetView(Context c){super(c);setLayerType(View.LAYER_TYPE_SOFTWARE,null);}
    void color(int c){p.setColor(c);p.setStyle(Paint.Style.FILL);}
    void oval(Canvas c,int col,float l,float t,float r,float b){color(col);c.drawOval(new RectF(l,t,r,b),p);}
    void path(Canvas c,int col,float... coords){
      color(col);Path q=new Path();q.moveTo(coords[0],coords[1]);
      for(int i=2;i<coords.length;i+=2)q.lineTo(coords[i],coords[i+1]);
      q.close();c.drawPath(q,p);
    }
    @Override protected void onDraw(Canvas real){
      super.onDraw(real);
      Canvas c=real;
      c.save();c.scale(getWidth()/300f,getHeight()/330f);
      // Static Ember Coat fallback: no continuous animation or GPU load.
      if(ember){
        oval(c,0x33FF6500,15,20,288,325);
        path(c,0x99FF5700,31,246,16,168,60,110,53,174,95,142,82,235);
        path(c,0xCCFF8C00,212,245,246,114,279,196,260,273);
        path(c,0xFFFFC13A,33,244,48,185,57,217,79,233);
        path(c,0xFFFFB224,230,255,256,188,260,246);
      }
      // Fluffy white pet: pink ears, round face, bright blue eyes, gemstone.
      oval(c,0x55B8E7FF,42,75,274,300);
      path(c,0xFFF7F8FF,48,142,52,24,129,91);
      path(c,0xFFF7F8FF,172,90,257,20,254,158);
      path(c,0xFFFFB9CE,69,118,73,55,117,100);
      path(c,0xFFFFB9CE,197,98,238,50,233,123);
      oval(c,0xFFFFFFFF,43,88,258,287);
      oval(c,0xFF83D4FA,75,151,130,210);
      oval(c,0xFF83D4FA,173,151,228,210);
      oval(c,0xFF184A9A,89,157,122,207);
      oval(c,0xFF184A9A,181,157,214,207);
      oval(c,Color.WHITE,97,162,110,179);
      oval(c,Color.WHITE,189,162,202,179);
      oval(c,0xFFFF9BB8,137,210,163,226);
      oval(c,0xFF543C58,135,229,166,244);
      oval(c,0xFFFFD0DE,53,212,84,231);
      oval(c,0xFFFFD0DE,217,212,248,231);
      if(ember){
        path(c,0xFFB42D0C,76,245,150,258,222,245,210,280,150,290,88,280);
        path(c,0xFFFF9D00,150,99,169,121,150,147,132,121);
      } else {
        path(c,0xFF2571DC,150,99,169,121,150,147,132,121);
      }
      path(c,0xFFB7F4FF,150,103,159,121,150,140,140,121);
      oval(c,0xFFFFFFFF,76,267,135,310);
      oval(c,0xFFFFFFFF,164,267,223,310);
      c.restore();
    }
  }
  // Preview only: never captures input or injects a click.
  public static class CursorPreviewView extends View {
    final Paint ink=new Paint(3);
    public CursorPreviewView(Context c){super(c);setLayerType(View.LAYER_TYPE_SOFTWARE,null);}
    @Override protected void onDraw(Canvas c){
      Path q=new Path();q.moveTo(4,3);q.lineTo(4,34);q.lineTo(12,26);
      q.lineTo(19,40);q.lineTo(25,37);q.lineTo(18,23);
      q.lineTo(30,23);q.close();
      ink.setStyle(Paint.Style.FILL);ink.setColor(0xFF17252D);c.drawPath(q,ink);
      ink.setStyle(Paint.Style.STROKE);ink.setColor(Color.WHITE);ink.setStrokeWidth(2f);
      c.drawPath(q,ink);
    }
  }
  public static class PetService extends Service {
    WindowManager wm;
    View pet;
    WindowManager.LayoutParams params;
    SurfaceView probeView;
    View previewCursor;
    WindowManager.LayoutParams cursorParams;
    final Handler cursorTimer=new Handler(Looper.getMainLooper());
    final Runnable cursorTimeout=new Runnable(){public void run(){hideCursor();}};
    void hideCursor(){
      cursorTimer.removeCallbacks(cursorTimeout);
      if(previewCursor!=null){wm.removeView(previewCursor);previewCursor=null;cursorParams=null;}
    }
    void showCursorPreview(int x,int y){
      DisplayMetrics size=new DisplayMetrics();wm.getDefaultDisplay().getMetrics(size);
      if(x<0||y<0||x>=size.widthPixels||y>=size.heightPixels)return;
      if(previewCursor==null){
        previewCursor=new CursorPreviewView(this);
        cursorParams=new WindowManager.LayoutParams(48,48,2038,
          WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE |
          WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
          PixelFormat.TRANSLUCENT);
        cursorParams.gravity=Gravity.TOP|Gravity.LEFT;
        wm.addView(previewCursor,cursorParams);
      }
      cursorParams.x=Math.max(0,Math.min(size.widthPixels-48,x));
      cursorParams.y=Math.max(0,Math.min(size.heightPixels-48,y));
      wm.updateViewLayout(previewCursor,cursorParams);
      cursorTimer.removeCallbacks(cursorTimeout);
      cursorTimer.postDelayed(cursorTimeout,3000L);
    }
    @Override public void onCreate(){
      super.onCreate();
      Notification n=new Notification.Builder(this)
        .setContentTitle("Alice Pet").setContentText("Pet overlay active")
        .setSmallIcon(android.R.drawable.star_on).build();
      startForeground(714,n);
      wm=(WindowManager)getSystemService(WINDOW_SERVICE);
      pet=new PetView(this);
      // Vulkan probe is opt-in; never replace the working Canvas surface.
      // SurfaceView will be attached as a separate window in the next stage.
      // Canvas stays visible; Vulkan probe runs only on a separate explicit surface later.
      params=new WindowManager.LayoutParams(
        300,330,2038,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE |
        WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE |
        WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.TRANSLUCENT);
      params.gravity=Gravity.TOP|Gravity.LEFT;
      params.x=1320;params.y=380;
      wm.addView(pet,params);
    }
    @Override public int onStartCommand(Intent i,int flags,int id){
      if(i!=null){
        String action=i.getStringExtra("pet_action");
        if("vulkan_probe".equals(action) && probeView==null){
          probeView=new SurfaceView(this);
          probeView.setZOrderOnTop(true);
          probeView.getHolder().setFormat(PixelFormat.TRANSLUCENT);
          probeView.getHolder().addCallback(new SurfaceHolder.Callback(){
            public void surfaceCreated(SurfaceHolder h){
              try { Log.i("AlicePetVulkan",((PetView)pet).probeVulkanSurface(h.getSurface())); }
              catch(Throwable t){Log.e("AlicePetVulkan","surface probe failed",t);}
            }
            public void surfaceChanged(SurfaceHolder h,int f,int w,int hgt){}
            public void surfaceDestroyed(SurfaceHolder h){}
          });
          WindowManager.LayoutParams probeParams=new WindowManager.LayoutParams(
            64,64,2038,WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE |
            WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,PixelFormat.TRANSLUCENT);
          probeParams.gravity=Gravity.TOP|Gravity.LEFT;
          probeParams.x=0;probeParams.y=0;
          wm.addView(probeView,probeParams);
        }
        // Authenticated caller must decide whether a preview may be shown.
        // Rendering this pointer never authorizes a root mouse operation.
        if("cursor_preview".equals(action))
          showCursorPreview(i.getIntExtra("x",-1),i.getIntExtra("y",-1));
        if("cursor_hide".equals(action))hideCursor();
        if("style".equals(action)){
          String style=i.getStringExtra("style");
          ((PetView)pet).setEmber("ember".equals(style));
        }
        if("hide".equals(action)) pet.setVisibility(View.GONE);
        if("show".equals(action)) pet.setVisibility(View.VISIBLE);
        if("move".equals(action)){
          params.x=Math.max(0,Math.min(2000,i.getIntExtra("x",params.x)));
          params.y=Math.max(0,Math.min(2000,i.getIntExtra("y",params.y)));
          wm.updateViewLayout(pet,params);
        }
        if("stop".equals(action)){stopSelf();return START_NOT_STICKY;}
      }
      return START_STICKY;
    }
    @Override public void onDestroy(){hideCursor();if(probeView!=null)wm.removeView(probeView);if(pet!=null)wm.removeView(pet);super.onDestroy();}
    @Override public IBinder onBind(Intent i){return null;}
  }
}
