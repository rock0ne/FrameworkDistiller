package uk.vijaukatt.frameworkdistiller;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.DocumentsContract;
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
import org.json.JSONObject;

import com.chaquo.python.Python;

import java.io.File;
import java.io.ByteArrayOutputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.io.UnsupportedEncodingException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int PICK_FILE = 40;
    private static final int SAVE_EXPORT = 41;
    private static final int PICK_FOLDER = 42;
    private static final int MAX_FOLDER_FILES = 300;
    private static final long MAX_FOLDER_BYTES = 1024L * 1024L * 1024L;
    private static final int MAX_DOCUMENT_BYTES = 25 * 1024 * 1024;
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
        settings.setUserAgentString(settings.getUserAgentString() + " FrameworkDistillerAndroid/0.5.5");
        webView.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
        webView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                if ("fd-folder".equals(url.getScheme()) && "choose".equals(url.getHost())) {
                    Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
                    picker.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    try { startActivityForResult(picker, PICK_FOLDER); }
                    catch (ActivityNotFoundException ignored) { toast("No folder picker is available"); }
                    return true;
                }
                if ("http".equals(url.getScheme()) && "127.0.0.1".equals(url.getHost())
                        && url.getPort() == localPort) return false;
                if ("mailto".equals(url.getScheme())) {
                    try { startActivity(new Intent(Intent.ACTION_SENDTO, url)); }
                    catch (ActivityNotFoundException ignored) { toast("No email app can open this draft"); }
                    return true;
                }
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
            String base = "http://127.0.0.1:" + localPort;
            boolean cards = url.startsWith(base + "/api/export?") || url.equals(base + "/api/export");
            boolean study = url.startsWith(base + "/api/study/export?");
            if (!cards && !study) return;
            boolean svg = study && url.contains("format=svg");
            pendingExportUrl = url;
            Intent save = new Intent(Intent.ACTION_CREATE_DOCUMENT);
            save.addCategory(Intent.CATEGORY_OPENABLE);
            save.setType(svg ? "image/svg+xml" : "text/markdown");
            save.putExtra(Intent.EXTRA_TITLE, svg ? "framework-study-map.svg" : "framework-study-notes.md");
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
        } else if (requestCode == PICK_FOLDER) {
            Uri uri = resultCode == RESULT_OK && data != null ? data.getData() : null;
            if (uri == null) folderStatus("Folder choice cancelled; nothing imported.", false);
            else worker.execute(() -> prepareFolderImport(uri));
        }
    }

    private static final class FolderEntry {
        final Uri uri;
        final String name;
        final long reportedSize;
        FolderEntry(Uri uri, String name, long reportedSize) {
            this.uri = uri; this.name = name; this.reportedSize = reportedSize;
        }
    }

    private static boolean supportedDocument(String name) {
        return name != null && name.length() <= 255 && !name.contains("/") && !name.contains("\\")
            && name.matches("(?i).+\\.(pdf|epub|docx|odt|pptx|rtf|md|markdown|txt|text|html|htm)");
    }

    private void folderStatus(String message, boolean done) {
        runOnUiThread(() -> {
            if (webView == null) return;
            String method = done ? "fdFolderImportDone" : "fdFolderImportStatus";
            webView.evaluateJavascript("window." + method + "(" + JSONObject.quote(message) + ")", null);
        });
    }

    private void collectFolder(Uri tree, String parentId, int depth, Set<String> visited,
                               List<FolderEntry> entries, int[] skipped) throws Exception {
        if (depth > 5 || visited.size() > 2000) throw new IllegalStateException("Folder is too deep or large");
        if (!visited.add(parentId)) return;
        Uri children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, parentId);
        String[] projection = {DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
            DocumentsContract.Document.COLUMN_SIZE};
        try (Cursor cursor = getContentResolver().query(children, projection, null, null, null)) {
            if (cursor == null) throw new IllegalStateException("Folder contents are unavailable");
            while (cursor.moveToNext()) {
                String id = cursor.getString(0);
                String name = cursor.getString(1);
                String mime = cursor.getString(2);
                long size = cursor.isNull(3) ? -1 : cursor.getLong(3);
                if (id == null) continue;
                if (DocumentsContract.Document.MIME_TYPE_DIR.equals(mime)) {
                    collectFolder(tree, id, depth + 1, visited, entries, skipped);
                } else if (supportedDocument(name) && size != 0 && size <= MAX_DOCUMENT_BYTES) {
                    if (entries.size() >= MAX_FOLDER_FILES) throw new IllegalStateException("More than 300 supported files; choose a smaller folder");
                    entries.add(new FolderEntry(DocumentsContract.buildDocumentUriUsingTree(tree, id), name, size));
                } else skipped[0] += 1;
            }
        }
    }

    private void prepareFolderImport(Uri tree) {
        try {
            List<FolderEntry> entries = new ArrayList<>();
            int[] skipped = {0};
            collectFolder(tree, DocumentsContract.getTreeDocumentId(tree), 0,
                new HashSet<>(), entries, skipped);
            entries.sort((a, b) -> a.name.compareToIgnoreCase(b.name));
            long knownTotal = 0;
            for (FolderEntry entry : entries) if (entry.reportedSize > 0) knownTotal += entry.reportedSize;
            if (knownTotal > MAX_FOLDER_BYTES) throw new IllegalStateException("Folder exceeds 1 GB; choose a smaller folder");
            if (entries.isEmpty()) { folderStatus("No supported files under 25 MB found in that folder.", false); return; }
            runOnUiThread(() -> new AlertDialog.Builder(this)
                .setTitle("Import selected folder?")
                .setMessage(entries.size() + " supported file(s) found; " + skipped[0]
                    + " skipped. Import copies locally and leaves originals unchanged. No AI provider receives them.")
                .setNegativeButton("Cancel", (dialog, which) -> folderStatus("Folder import cancelled; nothing changed.", false))
                .setPositiveButton("Import", (dialog, which) -> worker.execute(() -> importFolderEntries(entries, skipped[0])))
                .show());
        } catch (Exception error) {
            folderStatus("Folder cannot be imported: " + error.getMessage(), false);
        }
    }

    private byte[] readFolderFile(Uri uri, long[] totalRead) throws Exception {
        try (InputStream input = getContentResolver().openInputStream(uri)) {
            return FolderBatchReader.read(input, totalRead, MAX_DOCUMENT_BYTES, MAX_FOLDER_BYTES);
        }
    }

    private static String queryValue(String value) {
        try { return URLEncoder.encode(value, "UTF-8"); }
        catch (UnsupportedEncodingException impossible) { throw new AssertionError(impossible); }
    }

    private int postFolderFile(FolderEntry entry, byte[] bytes) throws Exception {
        String title = entry.name.replaceFirst("\\.[^.]+$", "");
        if (title.length() > 160) title = title.substring(0, 160);
        String origin = "http://127.0.0.1:" + localPort;
        String path = "/api/ingest/upload?name=" + queryValue(entry.name)
            + "&title=" + queryValue(title) + "&version=Local+copy&publication_date=Unknown"
            + "&source_url=&sha256=";
        HttpURLConnection connection = (HttpURLConnection) new URL(origin + path).openConnection();
        try {
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(10000);
            connection.setReadTimeout(180000);
            connection.setRequestMethod("POST");
            connection.setDoOutput(true);
            connection.setRequestProperty("Origin", origin);
            connection.setRequestProperty("Cookie", "fd_session=" + localSessionToken);
            connection.setRequestProperty("X-Local-Client", "FrameworkDistiller");
            connection.setRequestProperty("Content-Type", "application/octet-stream");
            connection.setFixedLengthStreamingMode(bytes.length);
            try (OutputStream output = connection.getOutputStream()) { output.write(bytes); }
            return connection.getResponseCode();
        } finally { connection.disconnect(); }
    }

    private static String folderCounts(int imported, int skipped, int failed,
                                       int unconfirmed, int unattempted) {
        return imported + " imported, " + skipped + " skipped, " + failed + " failed, "
            + unconfirmed + " unconfirmed, " + unattempted + " unattempted. Originals remain unchanged.";
    }

    private void importFolderEntries(List<FolderEntry> entries, int skippedBeforeRead) {
        int imported = 0, failed = 0, skipped = skippedBeforeRead, unconfirmed = 0;
        long[] totalRead = {0};
        for (int index = 0; index < entries.size(); index++) {
            if (totalRead[0] >= MAX_FOLDER_BYTES) {
                folderStatus(folderCounts(imported, skipped, failed, unconfirmed, entries.size() - index)
                    + " Stopped at the 1 GB batch limit; choose a smaller folder for the rest.", true);
                return;
            }
            FolderEntry entry = entries.get(index);
            folderStatus("Importing " + (index + 1) + " of " + entries.size() + ": " + entry.name, false);
            byte[] bytes;
            try {
                bytes = readFolderFile(entry.uri, totalRead);
            } catch (Exception error) {
                failed += 1;
                if (error instanceof FolderBatchReader.BatchLimitException || totalRead[0] >= MAX_FOLDER_BYTES) {
                    folderStatus(folderCounts(imported, skipped, failed, unconfirmed, entries.size() - index - 1)
                        + " Stopped at the 1 GB batch limit; choose a smaller folder for the rest.", true);
                    return;
                }
                continue;
            }
            if (bytes.length == 0) { skipped += 1; continue; }
            try {
                int status = postFolderFile(entry, bytes);
                if (status == 200) imported += 1;
                else {
                    failed += 1;
                    if (status == 403) {
                        folderStatus(folderCounts(imported, skipped, failed, unconfirmed, entries.size() - index - 1)
                            + " Local session expired. Reopen the app before retrying.", true);
                        return;
                    }
                }
            } catch (Exception error) {
                unconfirmed += 1;
                folderStatus(folderCounts(imported, skipped, failed, unconfirmed, entries.size() - index - 1)
                    + " Local service connection failed; this file may have imported. Reopen the app and check the library before retrying.", true);
                return;
            }
        }
        folderStatus(folderCounts(imported, skipped, failed, unconfirmed, 0), true);
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
