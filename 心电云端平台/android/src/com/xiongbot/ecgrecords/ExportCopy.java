package com.xiongbot.ecgrecords;

import java.io.InputStream;
import java.io.OutputStream;
import java.io.IOException;

/** Reject interrupted, empty or oversized exports before handing them to a document provider. */
public final class ExportCopy {
    public static long copy(InputStream input, OutputStream output, long expected, long limit) throws IOException {
        long total=0; byte[] buffer=new byte[32768]; int count;
        while((count=input.read(buffer))!=-1) {
            if(Thread.currentThread().isInterrupted())throw new IOException("下载已取消");
            total+=count;
            if(total>limit)throw new IOException("记录过大，请在电脑下载");
            output.write(buffer,0,count);
        }
        if(total==0 || (expected>=0 && total!=expected))throw new IOException("下载不完整，请重新下载");
        return total;
    }
    private ExportCopy() {}
}
