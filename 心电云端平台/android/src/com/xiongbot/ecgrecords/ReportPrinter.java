package com.xiongbot.ecgrecords;

import android.app.Activity;
import android.os.Bundle;
import android.os.CancellationSignal;
import android.os.ParcelFileDescriptor;
import android.print.PageRange;
import android.print.PrintAttributes;
import android.print.PrintDocumentAdapter;
import android.print.PrintJob;
import android.print.PrintManager;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

/** A single, user-requested report; no script, network, local file access or cache. */
final class ReportPrinter {
    private final Activity activity;
    private WebView view;
    private PrintJob job;
    ReportPrinter(Activity activity) { this.activity=activity; }

    void print(String html) {
        if(view!=null) { message("已有报告正在准备或打印，请完成后重试"); return; }
        if(html==null || html.length()>2*1024*1024 || !html.startsWith("<!doctype html>")) {
            message("报告暂不可用，请重新打开预览"); return;
        }
        final WebView paper=new WebView(activity);view=paper;
        paper.getSettings().setJavaScriptEnabled(false);
        paper.getSettings().setAllowFileAccess(false);
        paper.getSettings().setAllowContentAccess(false);
        paper.getSettings().setBlockNetworkLoads(true);
        paper.setWebViewClient(new WebViewClient() {
            private boolean started;
            @Override public void onPageFinished(WebView loaded,String url) {
                if(started || view!=paper || activity.isFinishing() || activity.isDestroyed())return;
                started=true;
                try {
                    PrintManager manager=(PrintManager)activity.getSystemService(Activity.PRINT_SERVICE);
                    if(manager==null)throw new IllegalStateException();
                    final PrintDocumentAdapter delegate=paper.createPrintDocumentAdapter("心电测量记录");
                    PrintDocumentAdapter adapter=new PrintDocumentAdapter() {
                        @Override public void onStart(){delegate.onStart();}
                        @Override public void onLayout(PrintAttributes oldAttrs,PrintAttributes newAttrs,CancellationSignal signal,LayoutResultCallback callback,Bundle extras){
                            delegate.onLayout(oldAttrs,newAttrs,signal,callback,extras);
                        }
                        @Override public void onWrite(PageRange[] pages,ParcelFileDescriptor target,CancellationSignal signal,WriteResultCallback callback){
                            delegate.onWrite(pages,target,signal,callback);
                        }
                        @Override public void onFinish(){try{delegate.onFinish();}finally{release(paper);}}
                    };
                    job=manager.print("心电测量记录",adapter,new PrintAttributes.Builder().build());
                } catch(Exception failure) {release(paper);message("无法打开系统打印，请稍后重试");}
            }
        });
        paper.loadDataWithBaseURL(null,html,"text/html","UTF-8",null);
    }
    private void message(String text){Toast.makeText(activity,text,Toast.LENGTH_LONG).show();}
    private void release(WebView paper){if(view==paper){view=null;paper.destroy();job=null;}}
    void close(){if(job!=null)job.cancel();if(view!=null)release(view);}
}
