#!/usr/bin/env bash
# Build the Android app with the SDK's own tools: no Gradle, no dependencies.
#   ANDROID_HOME        the Android SDK (needs a platform and build-tools)
#   ANDROID_KEYSTORE    keystore to sign with; ANDROID_KEYSTORE_PASS its password
# Without a keystore the APK is left unsigned in build/, and cannot be installed.
set -euo pipefail
# A relative keystore path is relative to where the script was called from, not to android/.
keystore=${ANDROID_KEYSTORE:+$(realpath "$ANDROID_KEYSTORE")}
cd "$(dirname "$0")"

VERSION_NAME=0.1.0
VERSION_CODE=1
MIN_SDK=29    # Android 10: the first with SslCertificate.getX509Certificate
TARGET_SDK=34 # shortcut: 35+ draws under the system bars; needs inset handling first

sdk=${ANDROID_HOME:?set ANDROID_HOME to the Android SDK}
tools=$(find "$sdk/build-tools" -mindepth 1 -maxdepth 1 -type d | sort -V | tail -n 1)
jar=$(find "$sdk/platforms" -name android.jar | sort -V | tail -n 1)
[ -x "$tools/aapt2" ] && [ -f "$jar" ] || {
    echo "build.sh: no build-tools or platform in $sdk" >&2
    exit 1
}

rm -rf build
mkdir -p build/gen build/classes build/test

# The address rules are plain Java: check them on this machine before packaging.
javac -d build/test src/cloud/theolympus/cryptotracker/Server.java test/ServerTest.java
java -ea -cp build/test cloud.theolympus.cryptotracker.ServerTest

"$tools/aapt2" compile --dir res -o build/res.zip
"$tools/aapt2" link -o build/unaligned.apk -I "$jar" --manifest AndroidManifest.xml \
    --java build/gen --min-sdk-version "$MIN_SDK" --target-sdk-version "$TARGET_SDK" \
    --version-code "$VERSION_CODE" --version-name "$VERSION_NAME" build/res.zip
find src build/gen -name '*.java' >build/sources.txt
javac -Xlint:all,-options -Werror -source 17 -target 17 -classpath "$jar" \
    -d build/classes @build/sources.txt
find build/classes -name '*.class' >build/classes.txt
"$tools/d8" --release --lib "$jar" --min-api "$MIN_SDK" --output build @build/classes.txt
jar --update --file build/unaligned.apk -C build classes.dex # the JDK's zip tool
"$tools/zipalign" -f 4 build/unaligned.apk build/aligned.apk

if [ -n "$keystore" ]; then
    "$tools/apksigner" sign --ks "$keystore" \
        --ks-pass "env:ANDROID_KEYSTORE_PASS" --out build/crypto-tracker.apk build/aligned.apk
    "$tools/apksigner" verify --print-certs build/crypto-tracker.apk | head -n 2
    echo "built android/build/crypto-tracker.apk"
else
    echo "built android/build/aligned.apk (unsigned: set ANDROID_KEYSTORE to sign it)"
fi
