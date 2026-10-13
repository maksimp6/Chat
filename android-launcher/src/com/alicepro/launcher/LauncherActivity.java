package com.alicepro.launcher;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.ResolveInfo;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.Editable;
import android.text.TextWatcher;
import android.text.format.DateFormat;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.EditText;
import android.widget.GridView;
import android.widget.ImageView;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/** Home screen: clock, Alice Pro shortcut, search and app grid with long-press actions. */
public class LauncherActivity extends Activity {
    private static final String ALICE_PACKAGE = "com.alicepro.mobile";
    private static final String ALICE_ACTIVITY = "com.alicepro.mobile.MainActivity";

    private final Handler handler = new Handler(Looper.getMainLooper());
    private TextView clock;
    private AgentStatusClient agentStatus;
    private EditText search;
    private GridView grid;
    private List<ResolveInfo> allApps;
    private final List<ResolveInfo> shown = new ArrayList<>();

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
        agentStatus = new AgentStatusClient(this, (TextView) findViewById(R.id.status));
        agentStatus.start();
        search = (EditText) findViewById(R.id.search);
        grid = (GridView) findViewById(R.id.apps);

        allApps = loadApps();
        shown.addAll(allApps);
        grid.setAdapter(new AppAdapter());

        search.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int a, int b, int c) { }
            @Override public void onTextChanged(CharSequence s, int a, int b, int c) { filter(s.toString()); }
            @Override public void afterTextChanged(Editable s) { }
        });

        grid.setOnItemClickListener((parent, view, position, id) -> {
            Intent launch = getPackageManager()
                    .getLaunchIntentForPackage(shown.get(position).activityInfo.packageName);
            if (launch != null) {
                startActivity(launch);
            }
        });
        grid.setOnItemLongClickListener((parent, view, position, id) -> {
            showActions(shown.get(position).activityInfo.packageName,
                    shown.get(position).loadLabel(getPackageManager()).toString());
            return true;
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
    protected void onDestroy() {
        super.onDestroy();
        agentStatus.stop();
    }

    @Override
    protected void onPause() {
        super.onPause();
        handler.removeCallbacks(tick);
    }

    private void filter(String query) {
        String q = query.trim().toLowerCase(Locale.ROOT);
        shown.clear();
        for (ResolveInfo info : allApps) {
            if (q.isEmpty() || info.loadLabel(getPackageManager()).toString().toLowerCase(Locale.ROOT).contains(q)) {
                shown.add(info);
            }
        }
        ((BaseAdapter) grid.getAdapter()).notifyDataSetChanged();
    }

    private void showActions(final String pkg, String label) {
        new AlertDialog.Builder(this)
                .setTitle(label)
                .setItems(new String[] {"Информация о приложении", "Удалить"}, (dialog, which) -> {
                    if (which == 0) {
                        Intent info = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                Uri.parse("package:" + pkg));
                        startActivity(info);
                    } else {
                        startActivity(new Intent(Intent.ACTION_DELETE, Uri.parse("package:" + pkg)));
                    }
                })
                .show();
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
            return shown.size();
        }

        @Override
        public Object getItem(int position) {
            return shown.get(position);
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
            ResolveInfo info = shown.get(position);
            ((ImageView) view.findViewById(R.id.icon)).setImageDrawable(info.loadIcon(getPackageManager()));
            ((TextView) view.findViewById(R.id.label)).setText(info.loadLabel(getPackageManager()));
            return view;
        }
    }
}
