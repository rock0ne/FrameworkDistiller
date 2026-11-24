package uk.vijaukatt.frameworkdistiller;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.WindowInsets;
import android.view.View;
import android.webkit.DownloadListener;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.Toast;

import com.chaquo.python.Python;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int PICK_FILE = 40;
    private static final int SAVE_EXPORT = 41;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private WebView webView;
    private android.webkit.ValueCallback<Uri[]> fileCallback;
    private String pendingExportUrl;
    private String localSessionToken;
    private int localPort;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        TextView loading = new TextView(this);
        loading.setText("Starting Framework Distiller locally…");
        loading.setTextSize(18);
        loading.setPadding(32, 48, 32, 32);
        setContentView(loading);
        worker.execute(() -> {
            try {
                File staticDir = new File(getFilesDir(), "static");
                if (!staticDir.isDirectory() && !staticDir.mkdirs()) {
                    throw new IllegalStateException("Cannot create local UI directory");
                }
                for (String name : new String[]{"index.html", "style.css", "app.js"}) {
                    copyAsset("static/" + name, new File(staticDir, name));
                }
                File dataDir = new File(getFilesDir(), "data");
                int port = Python.getInstance().getModule("bridge").callAttr(
                    "start", dataDir.getAbsolutePath(), staticDir.getAbsolutePath()).toInt();
                String token = Python.getInstance().getModule("bridge").callAttr("session_token").toString();
                runOnUiThread(() -> showReader(port, token));
            } catch (Exception error) {
                runOnUiThread(() -> loading.setText(
                    "Framework Distiller could not start: " + error.getClass().getSimpleName()
                    + " · " + error.getMessage()));
            }
        });
    }

    private void copyAsset(String name, File target) throws Exception {
        try (InputStream input = getAssets().open(name);
             OutputStream output = new FileOutputStream(target)) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
        }
    }

    private void showReader(int port, String token) {
        localPort = port;
        localSessionToken = token;
        webView = new WebView(this);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setUserAgentString(settings.getUserAgentString() + " FrameworkDistillerAndroid/0.4.1");
        webView.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
        webView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                if ("http".equals(url.getScheme()) && "127.0.0.1".equals(url.getHost())
                        && url.getPort() == localPort) return false;
                if ("https".equals(url.getScheme())) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, url)); }
                    catch (ActivityNotFoundException ignored) { toast("No browser can open this link"); }
                }
                return true;
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view,
                    android.webkit.ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                picker.addCategory(Intent.CATEGORY_OPENABLE);
                picker.setType("*/*");
                try { startActivityForResult(picker, PICK_FILE); return true; }
                catch (ActivityNotFoundException error) {
                    fileCallback.onReceiveValue(null);
                    fileCallback = null;
                    toast("No file picker is available");
                    return false;
                }
            }
        });
        webView.setDownloadListener((url, userAgent, disposition, mimeType, length) -> {
            String prefix = "http://127.0.0.1:" + localPort + "/api/export";
            if (!url.startsWith(prefix)) return;
            pendingExportUrl = url;
            Intent save = new Intent(Intent.ACTION_CREATE_DOCUMENT);
            save.addCategory(Intent.CATEGORY_OPENABLE);
            save.setType("text/plain");
            save.putExtra(Intent.EXTRA_TITLE, "framework-study-notes.md");
            try { startActivityForResult(save, SAVE_EXPORT); }
            catch (ActivityNotFoundException error) { toast("No document saver is available"); }
        });
        FrameLayout frame = new FrameLayout(this);
        frame.setBackgroundColor(0xff0d191d);
        frame.addView(webView, new FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        frame.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                frame.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            } else {
                frame.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                                 insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            }
            return insets;
        });
        setContentView(frame);
        // The fragment is not sent over HTTP; app.js exchanges it for an
        // HttpOnly local-session cookie, then removes it from WebView history.
        webView.loadUrl("http://127.0.0.1:" + port + "/#launch=" + token);
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == PICK_FILE && fileCallback != null) {
            Uri uri = resultCode == RESULT_OK && data != null ? data.getData() : null;
            fileCallback.onReceiveValue(uri == null ? null : new Uri[]{uri});
            fileCallback = null;
        } else if (requestCode == SAVE_EXPORT) {
            Uri uri = resultCode == RESULT_OK && data != null ? data.getData() : null;
            String url = pendingExportUrl;
            pendingExportUrl = null;
            if (uri != null && url != null) worker.execute(() -> saveExport(url, uri));
        }
    }

    private void saveExport(String address, Uri destination) {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(address);
            connection = (HttpURLConnection) url.openConnection();
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(10000);
            connection.setRequestProperty("Cookie", "fd_session=" + localSessionToken);
            if (connection.getResponseCode() != 200) throw new IllegalStateException("Local export failed");
            try (InputStream input = connection.getInputStream();
                 OutputStream output = getContentResolver().openOutputStream(destination)) {
                if (output == null) throw new IllegalStateException("Cannot write selected file");
                byte[] buffer = new byte[8192];
                int count;
                while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            }
            toast("Study notes saved");
        } catch (Exception error) {
            toast("Export failed: " + error.getClass().getSimpleName());
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private void toast(String message) {
        runOnUiThread(() -> Toast.makeText(this, message, Toast.LENGTH_SHORT).show());
    }

    @Override public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override protected void onDestroy() {
        if (webView != null) webView.destroy();
        worker.execute(() -> Python.getInstance().getModule("bridge").callAttr("clear_keys"));
        worker.shutdown();
        super.onDestroy();
    }
}
