model_reference.npz: 原验证缓存 val_det_y 每个目标类别前12拍，共36拍；proba来自原始三个种子的 validation_probabilities.npy 概率平均，不由新部署代码生成。
stream_reference.npz: 30秒合成波形，由完整算法链路与改进/scripts/pipeline.py中的原检测器和原预处理函数生成的检测峰、归一化心拍和RR参考；仅提取原函数执行以避免导入无关WFDB依赖。
proba_cpu: 使用原网络源文件及原权重，在原PyTorch 2.13 CPU环境重算的36拍参考；与原GPU保存概率最大差0.00028950、类别一致率100%，用于区分跨设备浮点差异和部署代码差异。
