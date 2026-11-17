package cloud.theolympus.cryptotracker;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.os.Parcelable;
import android.provider.MediaStore;
import android.text.InputType;
import android.webkit.CookieManager;
import android.webkit.HttpAuthHandler;
import android.webkit.PermissionRequest;
import android.webkit.SslErrorHandler;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebViewDatabase;
import android.widget.EditText;
import java.security.cert.CertificateEncodingException;
import java.security.cert.X509Certificate;

/**
 * Shows the server's own web app in a WebView: there is no second front end. The app adds
 * what a browser tab cannot be trusted or asked to do for a self-hosted server: remember the
 * password, accept one self-signed certificate by its fingerprint, and pass the camera on.
 */
public class MainActivity extends Activity {
    private static final int ASK_CAMERA_FOR_SCAN = 1;
    private static final int ASK_CAMERA_FOR_PHOTO = 2;
    private static final int PICK_PHOTO = 3;

    private SharedPreferences prefs;
    private String base;
    private WebView web;
    private PermissionRequest scan; // a page waiting for the camera permission
    private ValueCallback<Uri[]> photoFor; // a page waiting for a photo
    private Intent picker;
    private boolean asking; // one certificate or error dialog at a time

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences(SetupActivity.PREFS, MODE_PRIVATE);
        base = prefs.getString(SetupActivity.URL, null);
        boolean settings = "cloud.theolympus.cryptotracker.SETTINGS".equals(getIntent().getAction());
        if (base == null || settings) {
            startActivity(new Intent(this, SetupActivity.class));
            finish();
            return;
        }
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false); // the QR scanner's camera preview
        s.setAllowFileAccess(false);
        web.setWebViewClient(new Pages());
        web.setWebChromeClient(new Device());
        setContentView(web);
        // WebView remembers an accepted certificate for as long as the process lives: when
        // none is accepted (any more), make it ask again.
        if (prefs.getString(SetupActivity.CERT, null) == null) web.clearSslPreferences();
        PhotoProvider.file(this).delete(); // a QR photo from an earlier run
        if (state == null || web.restoreState(state) == null) web.loadUrl(base);
    }

    @Override
    protected void onPause() {
        super.onPause();
        // The server signs the app in with a cookie after one good password: keep it on disk
        // now, not at the WebView's next periodic write, in case the app is killed.
        CookieManager.getInstance().flush();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        if (web != null) web.saveState(out);
    }

    // shortcut: the old back callback, still honoured at this target SDK; move to
    // OnBackInvokedCallback when the target is raised and predictive back is switched on.
    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    private void openSettings() {
        startActivity(new Intent(this, SetupActivity.class));
        finish();
    }

    private final class Pages extends WebViewClient {
        /** Only the server's own pages stay in the app; an explorer link opens in the browser. */
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            String url = request.getUrl().toString();
            if (Server.sameOrigin(base, url)) return false;
            String scheme = request.getUrl().getScheme();
            if ("https".equals(scheme) || "http".equals(scheme)) {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, request.getUrl()));
                } catch (ActivityNotFoundException ignored) {
                    // no browser installed: nothing to open it with
                }
            }
            return true;
        }

        /** HTTP Basic: ask once, then answer from the WebView's private store. */
        @Override
        public void onReceivedHttpAuthRequest(
                WebView view, final HttpAuthHandler handler, final String host, final String realm) {
            // Only the configured server is ever given, or asked for, the password.
            if (!host.equalsIgnoreCase(Uri.parse(base).getHost())) {
                handler.cancel();
                return;
            }
            final WebViewDatabase store = WebViewDatabase.getInstance(MainActivity.this);
            // useHttpAuthUsernamePassword() is false after a stored password was refused.
            String[] saved = handler.useHttpAuthUsernamePassword()
                    ? store.getHttpAuthUsernamePassword(host, realm) : null;
            if (saved != null) {
                handler.proceed(saved[0], saved[1]);
                return;
            }
            final EditText password = new EditText(MainActivity.this);
            password.setInputType(
                    InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
            password.setHint("Password");
            new AlertDialog.Builder(MainActivity.this)
                    .setTitle("Password of " + host)
                    .setMessage("It is remembered on this phone. \"Forget\" is in Server settings.")
                    .setView(password)
                    .setCancelable(false)
                    .setPositiveButton("Sign in", (d, w) -> {
                        String typed = password.getText().toString();
                        store.setHttpAuthUsernamePassword(host, realm, "user", typed);
                        handler.proceed("user", typed); // the server checks the password only
                    })
                    .setNegativeButton("Cancel", (d, w) -> handler.cancel())
                    .show();
        }

        /**
         * A certificate the system does not trust is refused, unless the user said the server
         * is self-signed and accepted exactly this certificate by its fingerprint.
         */
        @Override
        public void onReceivedSslError(WebView view, final SslErrorHandler handler, SslError error) {
            final String seen = fingerprint(error);
            String accepted = prefs.getString(SetupActivity.CERT, null);
            if (seen == null || !Server.sameOrigin(base, error.getUrl())) {
                handler.cancel();
            } else if (seen.equals(accepted)) {
                handler.proceed();
            } else if (asking) {
                handler.cancel(); // the page is reloaded once the open question is answered
            } else if (accepted != null) {
                handler.cancel();
                problem("The server's certificate changed",
                        "It is not the one you accepted, so nothing was sent. If you replaced "
                                + "the certificate yourself, use \"Forget\" in Server settings.");
            } else if (!prefs.getBoolean(SetupActivity.SELF_SIGNED, false)) {
                handler.cancel();
                problem("The server's certificate is not trusted",
                        "If it is self-signed, say so in Server settings.");
            } else {
                handler.cancel();
                asking = true;
                new AlertDialog.Builder(MainActivity.this)
                        .setTitle("Trust this certificate?")
                        .setMessage("SHA-256 fingerprint:\n\n" + seen + "\n\nCompare it with the "
                                + "one on your server. Accept only if they are the same.")
                        .setCancelable(false)
                        .setPositiveButton("They match", (d, w) -> {
                            asking = false;
                            prefs.edit().putString(SetupActivity.CERT, seen).apply();
                            web.loadUrl(base);
                        })
                        .setNegativeButton("Do not connect", (d, w) -> asking = false)
                        .show();
            }
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request.isForMainFrame()) {
                problem("Cannot reach the server", base + "\n\n" + error.getDescription());
            }
        }
    }

    private static String fingerprint(SslError error) {
        X509Certificate certificate = error.getCertificate().getX509Certificate();
        if (certificate == null) return null;
        try {
            return Server.fingerprint(certificate.getEncoded());
        } catch (CertificateEncodingException e) {
            return null;
        }
    }

    private void problem(String title, String message) {
        if (asking || isFinishing()) return;
        asking = true;
        new AlertDialog.Builder(this)
                .setTitle(title)
                .setMessage(message)
                .setCancelable(false)
                .setPositiveButton("Try again", (d, w) -> {
                    asking = false;
                    web.loadUrl(base);
                })
                .setNeutralButton("Server settings", (d, w) -> openSettings())
                .setNegativeButton("Close", (d, w) -> asking = false)
                .show();
    }

    private boolean hasCamera() {
        return checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED;
    }

    private final class Device extends WebChromeClient {
        /** The live QR scan. Only the server's own pages may have the camera, and only it. */
        @Override
        public void onPermissionRequest(PermissionRequest request) {
            boolean camera = false;
            for (String resource : request.getResources()) {
                camera |= PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource);
            }
            if (!camera || !Server.sameOrigin(base, request.getOrigin().toString())) {
                request.deny();
            } else if (hasCamera()) {
                request.grant(new String[] {PermissionRequest.RESOURCE_VIDEO_CAPTURE});
            } else {
                if (scan != null) scan.deny(); // an earlier request this one replaces
                scan = request;
                requestPermissions(new String[] {Manifest.permission.CAMERA}, ASK_CAMERA_FOR_SCAN);
            }
        }

        @Override
        public void onPermissionRequestCanceled(PermissionRequest request) {
            if (request == scan) scan = null;
        }

        /** "Take a photo": the scanner's fallback where the page is not allowed a live camera. */
        @Override
        public boolean onShowFileChooser(
                WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
            if (photoFor != null) photoFor.onReceiveValue(null);
            photoFor = callback;
            picker = params.createIntent();
            if (hasCamera()) choosePhoto();
            else requestPermissions(new String[] {Manifest.permission.CAMERA}, ASK_CAMERA_FOR_PHOTO);
            return true;
        }
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        if (code == ASK_CAMERA_FOR_SCAN && scan != null) {
            if (hasCamera()) scan.grant(new String[] {PermissionRequest.RESOURCE_VIDEO_CAPTURE});
            else scan.deny();
            scan = null;
        } else if (code == ASK_CAMERA_FOR_PHOTO) {
            choosePhoto(); // without the camera, a picture already on the phone still works
        }
    }

    private void choosePhoto() {
        Intent chooser = Intent.createChooser(picker, "Photo of the QR code");
        PhotoProvider.file(this).delete();
        if (hasCamera()) {
            // The camera app writes into the app's private file, never into the gallery.
            Intent camera = new Intent(MediaStore.ACTION_IMAGE_CAPTURE)
                    .putExtra(MediaStore.EXTRA_OUTPUT, PhotoProvider.PHOTO)
                    .addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                            | Intent.FLAG_GRANT_READ_URI_PERMISSION);
            camera.setClipData(ClipData.newRawUri(null, PhotoProvider.PHOTO));
            chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Parcelable[] {camera});
        }
        try {
            startActivityForResult(chooser, PICK_PHOTO);
        } catch (ActivityNotFoundException e) {
            onActivityResult(PICK_PHOTO, RESULT_CANCELED, null);
        }
    }

    @Override
    protected void onActivityResult(int code, int result, Intent data) {
        if (code != PICK_PHOTO || photoFor == null) return;
        Uri picked = result == RESULT_OK && data != null ? data.getData() : null;
        // A camera app answers with no address, or with the one it was given.
        boolean taken = result == RESULT_OK
                && (picked == null || PhotoProvider.PHOTO.equals(picked))
                && PhotoProvider.file(this).length() > 0;
        if (!taken) PhotoProvider.file(this).delete();
        Uri chosen = taken ? PhotoProvider.PHOTO : PhotoProvider.PHOTO.equals(picked) ? null : picked;
        photoFor.onReceiveValue(chosen == null ? null : new Uri[] {chosen});
        photoFor = null;
    }
}
