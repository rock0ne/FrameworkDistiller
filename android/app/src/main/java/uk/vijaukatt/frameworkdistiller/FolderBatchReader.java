package uk.vijaukatt.frameworkdistiller;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;

/** Counts bytes at the read boundary, including bytes consumed before a failed file. */
final class FolderBatchReader {
    static final class BatchLimitException extends IOException {
        BatchLimitException() { super("Folder exceeds the 1 GB batch limit"); }
    }

    private FolderBatchReader() { }

    static byte[] read(InputStream input, long[] totalRead, int fileLimit, long batchLimit)
            throws IOException {
        if (input == null) throw new IOException("File provider did not return data");
        if (totalRead[0] >= batchLimit) throw new BatchLimitException();
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int zeroReads = 0;
        while (true) {
            long batchRemaining = batchLimit - totalRead[0];
            int fileRemaining = fileLimit - output.size();
            if (batchRemaining <= 0 || fileRemaining <= 0) {
                // One byte is the bounded EOF probe. Charge it if the stream has
                // more content, even when the provider's declared size was wrong.
                int probe = input.read();
                if (probe == -1) return output.toByteArray();
                totalRead[0] += 1;
                if (batchRemaining <= 0) throw new BatchLimitException();
                throw new IOException("File exceeds 25 MB");
            }
            int allowance = (int) Math.min(buffer.length, Math.min(batchRemaining, fileRemaining));
            int count = input.read(buffer, 0, allowance);
            if (count == -1) return output.toByteArray();
            if (count == 0) {
                if (++zeroReads >= 3) throw new IOException("File provider made no read progress");
                continue;
            }
            zeroReads = 0;
            totalRead[0] += count;
            output.write(buffer, 0, count);
        }
    }
}
