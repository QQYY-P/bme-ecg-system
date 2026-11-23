(function(root){
  'use strict';
  function describeFailure(error,status=0,online=true){
    if(!online)return {kind:'offline',title:'网络已断开',message:'请恢复电脑网络连接，再点击重新连接。'};
    if(status===401)return {kind:'expired',title:'请重新登录',message:'登录已过期，请使用自己的账号重新登录。'};
    if(error?.name==='TimeoutError'||error?.name==='AbortError')return {kind:'timeout',title:'连接超时',message:'服务器响应较慢，请稍后重试。'};
    if(status>=500)return {kind:'server',title:'云端服务暂不可用',message:'服务器暂时无法处理请求，请稍后重试；持续失败时联系管理员。'};
    return {kind:'unreachable',title:'暂时无法连接云端',message:'请检查网络和服务器状态后重试。如浏览器提示证书异常，请联系管理员。'};
  }
  const api={describeFailure};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ECGConnection=api;
})(typeof globalThis!=='undefined'?globalThis:this);
