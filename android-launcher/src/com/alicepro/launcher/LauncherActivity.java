package com.alicepro.launcher;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.ResolveInfo;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.format.DateFormat;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.GridView;
import android.widget.ImageView;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.List;

/** Minimal home screen: clock, Alice Pro shortcut and a grid of launchable apps. */
public class LauncherActivity extends Activity {
    private static final String ALICE_PACKAGE = "com.alicepro.mobile";
    private static final String ALICE_ACTIVITY = "com.alicepro.mobile.MainActivity";

    private final Handler handler = new Handler(Looper.getMainLooper());
    private TextView clock;
    private List<ResolveInfo> apps;

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            clock.setText(DateFormat.format("HH:mm  EEE, d MMM", new Date()));
            handler.postDelayed(this, 30000L);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_launcher);
        clock = (TextView) findViewById(R.id.clock);

        apps = loadApps();
        GridView grid = (GridView) findViewById(R.id.apps);
        grid.setAdapter(new AppAdapter());
        grid.setOnItemClickListener((parent, view, position, id) -> {
            Intent launch = getPackageManager()
                    .getLaunchIntentForPackage(apps.get(position).activityInfo.packageName);
            if (launch != null) {
                startActivity(launch);
            }
        });

        findViewById(R.id.alice).setOnClickListener(v -> {
            Intent intent = new Intent();
            intent.setClassName(ALICE_PACKAGE, ALICE_ACTIVITY);
            startActivity(intent);
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        handler.post(tick);
    }

    @Override
    protected void onPause() {
        super.onPause();
        handler.removeCallbacks(tick);
    }

    private List<ResolveInfo> loadApps() {
        Intent intent = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        List<ResolveInfo> list = new ArrayList<>(getPackageManager().queryIntentActivities(intent, 0));
        list.removeIf(r -> r.activityInfo.packageName.equals(getPackageName()));
        Collections.sort(list, (a, b) -> a.loadLabel(getPackageManager()).toString()
                .compareToIgnoreCase(b.loadLabel(getPackageManager()).toString()));
        return list;
    }

    private class AppAdapter extends BaseAdapter {
        @Override
        public int getCount() {
            return apps.size();
        }

        @Override
        public Object getItem(int position) {
            return apps.get(position);
        }

        @Override
        public long getItemId(int position) {
            return position;
        }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            View view = convertView;
            if (view == null) {
                view = LayoutInflater.from(LauncherActivity.this).inflate(R.layout.item_app, parent, false);
            }
            ResolveInfo info = apps.get(position);
            ((ImageView) view.findViewById(R.id.icon)).setImageDrawable(info.loadIcon(getPackageManager()));
            ((TextView) view.findViewById(R.id.label)).setText(info.loadLabel(getPackageManager()));
            return view;
        }
    }
}
