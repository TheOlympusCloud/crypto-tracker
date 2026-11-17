package cloud.theolympus.cryptotracker;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.os.Process;
import android.text.InputType;
import android.webkit.WebViewDatabase;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/** Asks where the server is. Shown on first launch and from the app icon's shortcut. */
public class SetupActivity extends Activity {
    static final String PREFS = "server";
    static final String URL = "url"; // normalised address of the server
    static final String SELF_SIGNED = "selfsigned"; // the user said its certificate is self-signed
    static final String CERT = "cert"; // fingerprint of the certificate the user accepted

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        final SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        String saved = prefs.getString(URL, "");
        int pad = (int) (20 * getResources().getDisplayMetrics().density);

        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(pad, pad, pad, pad);

        TextView intro = new TextView(this);
        intro.setText("Where is your Crypto tracker server?");
        intro.setTextSize(20);
        form.addView(intro);

        final EditText address = new EditText(this);
        address.setHint("https://tracker.example  or  192.168.1.20:8000");
        address.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        address.setSingleLine(true);
        address.setText(saved);
        form.addView(address);

        final CheckBox http = new CheckBox(this);
        http.setText("Allow plain http. Nothing is encrypted: anyone on the same network can "
                + "read your wallets and the password.");
        http.setChecked(saved.startsWith("http://"));
        form.addView(http);

        final CheckBox selfSigned = new CheckBox(this);
        selfSigned.setText("My server uses a self-signed certificate. I will compare its "
                + "fingerprint when the app shows it.");
        selfSigned.setChecked(prefs.getBoolean(SELF_SIGNED, false));
        form.addView(selfSigned);

        final TextView error = new TextView(this);
        error.setTextColor(0xFFC62828);
        form.addView(error);

        Button save = new Button(this);
        save.setText("Connect");
        save.setOnClickListener(v -> {
            String url;
            try {
                url = Server.normalise(address.getText().toString(), http.isChecked());
            } catch (IllegalArgumentException e) {
                error.setText(e.getMessage());
                return;
            }
            SharedPreferences.Editor edit = prefs.edit()
                    .putString(URL, url)
                    .putBoolean(SELF_SIGNED, selfSigned.isChecked());
            // Another server, or no longer self-signed: an accepted certificate does not carry over.
            if (!url.equals(prefs.getString(URL, "")) || !selfSigned.isChecked()) edit.remove(CERT);
            edit.apply();
            startActivity(new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP));
            finish();
        });
        form.addView(save);

        Button forget = new Button(this);
        forget.setText("Forget the saved password and certificate, and close the app");
        forget.setOnClickListener(v -> {
            WebViewDatabase.getInstance(this).clearHttpAuthUsernamePassword();
            prefs.edit().remove(CERT).commit(); // written before the process ends
            // The WebView also keeps both in memory until its process ends: end it, so that
            // "forget" is true from this moment and not from the next restart.
            finishAffinity();
            Process.killProcess(Process.myPid());
        });
        form.addView(forget);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(form);
        setContentView(scroll);
    }
}
