package uk.vijaukatt.frameworkdistiller;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;

public final class FolderBatchReaderTest {
    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static InputStream bytes(int length) {
        return new ByteArrayInputStream(new byte[length]);
    }

    public static void main(String[] args) throws Exception {
        long[] total = {0};
        check(FolderBatchReader.read(bytes(6), total, 10, 16).length == 6, "first file");
        check(FolderBatchReader.read(bytes(10), total, 10, 16).length == 10, "exact EOF");
        check(total[0] == 16, "exact batch bytes charged");
        try {
            FolderBatchReader.read(bytes(1), total, 10, 16);
            throw new AssertionError("must stop before another file");
        } catch (FolderBatchReader.BatchLimitException expected) { }

        total[0] = 0;
        InputStream failing = new InputStream() {
            private int count = 0;
            @Override public int read() throws IOException {
                if (count == 7) throw new IOException("provider disconnected");
                count++;
                return 65;
            }
            @Override public int read(byte[] buffer, int offset, int length) throws IOException {
                buffer[offset] = (byte) read();
                return 1;
            }
        };
        try {
            FolderBatchReader.read(failing, total, 10, 16);
            throw new AssertionError("provider failure expected");
        } catch (IOException expected) { }
        check(total[0] == 7, "failed provider reads remain charged");
        try {
            FolderBatchReader.read(bytes(10), total, 10, 16);
            throw new AssertionError("batch overflow expected");
        } catch (FolderBatchReader.BatchLimitException expected) { }
        check(total[0] == 17, "only one EOF/overflow probe above batch limit");

        total[0] = 0;
        try {
            FolderBatchReader.read(bytes(11), total, 10, 100);
            throw new AssertionError("file overflow expected");
        } catch (IOException expected) { }
        check(total[0] == 11, "per-file overflow probe charged");

        total[0] = 0;
        InputStream stalled = new InputStream() {
            @Override public int read() { return 0; }
            @Override public int read(byte[] buffer, int offset, int length) { return 0; }
        };
        try {
            FolderBatchReader.read(stalled, total, 10, 100);
            throw new AssertionError("stalled provider expected");
        } catch (IOException expected) {
            check(expected.getMessage().contains("no read progress"), "bounded stalled read");
        }
        System.out.println("FolderBatchReader bounded-read checks passed");
    }
}
