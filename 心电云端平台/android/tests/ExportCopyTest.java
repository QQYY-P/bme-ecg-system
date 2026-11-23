package com.xiongbot.ecgrecords;
import java.io.*;
public final class ExportCopyTest {
    public static void main(String[] args) throws Exception {
        byte[] csv="sample_id,adc\n0,2048\n".getBytes("UTF-8");
        for(long expected:new long[]{csv.length,-1}) {
            ByteArrayOutputStream out=new ByteArrayOutputStream();
            if(ExportCopy.copy(new ByteArrayInputStream(csv),out,expected,1000)!=csv.length || !java.util.Arrays.equals(csv,out.toByteArray()))throw new AssertionError();
        }
        for(long[] bounds:new long[][]{{csv.length+1,1000},{csv.length,3}}) {
            try {ExportCopy.copy(new ByteArrayInputStream(csv),new ByteArrayOutputStream(),bounds[0],bounds[1]);throw new AssertionError();}
            catch(IOException expected) {}
        }
        try {ExportCopy.copy(new ByteArrayInputStream(new byte[0]),new ByteArrayOutputStream(),-1,1000);throw new AssertionError();}
        catch(IOException expected) {}
        try {ExportCopy.copy(new InputStream(){public int read()throws IOException{throw new IOException("interrupted network");}},new ByteArrayOutputStream(),-1,1000);throw new AssertionError();}
        catch(IOException expected) {}
        System.out.println("ExportCopy: 6 cases passed");
    }
}
