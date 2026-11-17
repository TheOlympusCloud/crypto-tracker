package cloud.theolympus.cryptotracker;

import java.net.URI;
import java.net.URISyntaxException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Locale;

/** The rules about the server address. Plain Java, so ServerTest can run it on a desktop. */
final class Server {
    private Server() {}

    /**
     * The address to load, from what the user typed. Throws IllegalArgumentException with
     * a message meant for the user. Plain http is refused unless it was allowed.
     */
    static String normalise(String typed, boolean allowHttp) {
        String s = typed == null ? "" : typed.trim();
        if (s.isEmpty()) throw new IllegalArgumentException("Enter the address of your server.");
        // "192.168.1.20:8000" is a LAN server over http when http was allowed, else https.
        if (!s.contains("://")) s = (allowHttp ? "http://" : "https://") + s;
        URI u;
        try {
            u = new URI(s);
        } catch (URISyntaxException e) {
            throw new IllegalArgumentException("That is not a valid address.");
        }
        String scheme = u.getScheme() == null ? "" : u.getScheme().toLowerCase(Locale.ROOT);
        if (u.getHost() == null) throw new IllegalArgumentException("That is not a valid address.");
        if (u.getUserInfo() != null) {
            throw new IllegalArgumentException(
                    "Leave the password out of the address: the app asks for it.");
        }
        if (scheme.equals("http")) {
            if (!allowHttp) {
                throw new IllegalArgumentException(
                        "Plain http is not encrypted. Tick the box to allow it, or use https.");
            }
        } else if (!scheme.equals("https")) {
            throw new IllegalArgumentException("The address must start with http:// or https://.");
        }
        String path = u.getRawPath() == null || u.getRawPath().isEmpty() ? "/" : u.getRawPath();
        if (!path.endsWith("/")) path += "/";
        return scheme + "://" + u.getRawAuthority().toLowerCase(Locale.ROOT) + path;
    }

    /** True when `url` is on the server at `base`: same scheme, host and port. */
    static boolean sameOrigin(String base, String url) {
        try {
            URI a = new URI(base);
            URI b = new URI(url);
            return a.getScheme() != null && a.getHost() != null
                    && a.getScheme().equalsIgnoreCase(b.getScheme())
                    && a.getHost().equalsIgnoreCase(b.getHost())
                    && port(a) == port(b);
        } catch (URISyntaxException e) {
            return false;
        }
    }

    private static int port(URI u) {
        if (u.getPort() != -1) return u.getPort();
        return "https".equalsIgnoreCase(u.getScheme()) ? 443 : 80;
    }

    /** SHA-256 of a certificate as "AB:CD:…", the form a browser or openssl shows. */
    static String fingerprint(byte[] der) {
        try {
            StringBuilder out = new StringBuilder();
            for (byte b : MessageDigest.getInstance("SHA-256").digest(der)) {
                if (out.length() > 0) out.append(':');
                out.append(String.format(Locale.ROOT, "%02X", b));
            }
            return out.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
