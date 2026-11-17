package cloud.theolympus.cryptotracker;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileNotFoundException;

/**
 * Hands the camera app one private file to write a photo of a QR code into. The photo shows
 * a wallet address, so it must not land in the shared gallery: it lives in the app's cache,
 * is reachable only through a permission granted for that one capture, and is deleted when
 * the app next starts or takes another.
 */
public final class PhotoProvider extends ContentProvider {
    static final Uri PHOTO = Uri.parse("content://cloud.theolympus.cryptotracker.photo/qr.jpg");

    static File file(Context context) {
        return new File(context.getCacheDir(), "qr.jpg");
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!PHOTO.equals(uri)) throw new FileNotFoundException(uri.toString());
        return ParcelFileDescriptor.open(file(getContext()), ParcelFileDescriptor.parseMode(mode));
    }

    @Override
    public String getType(Uri uri) {
        return "image/jpeg";
    }

    /** Name and size, which is what a reader of a picked file asks for. */
    @Override
    public Cursor query(Uri uri, String[] columns, String where, String[] args, String order) {
        MatrixCursor row = new MatrixCursor(
                new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE});
        if (PHOTO.equals(uri)) row.addRow(new Object[] {"qr.jpg", file(getContext()).length()});
        return row;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        return null;
    }

    @Override
    public int update(Uri uri, ContentValues values, String where, String[] args) {
        return 0;
    }

    @Override
    public int delete(Uri uri, String where, String[] args) {
        return 0;
    }
}
