package cloud.theolympus.cryptotracker;

/** Run by build.sh with assertions on, before the APK is built. */
public final class ServerTest {
    public static void main(String[] args) {
        eq(Server.normalise(" Tracker.Example ", false), "https://tracker.example/");
        eq(Server.normalise("https://tracker.example/app", false), "https://tracker.example/app/");
        eq(Server.normalise("192.168.1.20:8000", true), "http://192.168.1.20:8000/");
        eq(Server.normalise("192.168.1.20:8000", false), "https://192.168.1.20:8000/");
        eq(Server.normalise("http://[fd00::5]:8000", true), "http://[fd00::5]:8000/");
        for (String bad : new String[] {"", "http://lan:8000", "ftp://x", "https://me:pw@x", "https://", "a b"}) {
            try {
                Server.normalise(bad, false);
                throw new AssertionError("accepted: " + bad);
            } catch (IllegalArgumentException expected) {
                if (expected.getMessage().isEmpty()) throw new AssertionError("no message");
            }
        }
        String base = "https://tracker.example/";
        yes(Server.sameOrigin(base, "https://TRACKER.example:443/api/graph"));
        yes(!Server.sameOrigin(base, "http://tracker.example/"));
        yes(!Server.sameOrigin(base, "https://tracker.example.evil.test/"));
        yes(!Server.sameOrigin(base, "https://tracker.example:8443/"));
        yes(!Server.sameOrigin(base, "https://etherscan.io/address/0x0"));
        yes(!Server.sameOrigin(base, "intent://x#Intent;end") && !Server.sameOrigin(base, "not a url"));
        eq(Server.fingerprint(new byte[0]).substring(0, 11), "E3:B0:C4:42");
        yes(Server.fingerprint(new byte[] {1}).length() == 32 * 3 - 1);
        System.out.println("ServerTest ok");
    }

    private static void eq(String got, String want) {
        if (!got.equals(want)) throw new AssertionError(got + " != " + want);
    }

    private static void yes(boolean condition) {
        if (!condition) throw new AssertionError();
    }
}
