## Getting Started with CAVIS

This document provides a brief intro of the usage of CAVIS.

Please see [Getting Started with Detectron2](https://github.com/facebookresearch/detectron2/blob/master/GETTING_STARTED.md) for full usage.

### Training
We provide a script `train_net_video.py`, that is made to train all the configs provided in CAVIS.

To train a model with "train_net_video.py", first setup the corresponding datasets following
[datasets/README.md](./datasets/README.md), then download the pre-trained weights from [here](MODEL_ZOO.md) and put them in the current working directory.
Once these are set up, run:
```
# train the CAVIS_Segmenter
python train_net_video.py \
  --num-gpus 8 \
  --config-file /path/to/CAVIS_Segmenter_config_file.yaml \
  --resume MODEL.WEIGHTS /path/to/coco_pretrained_weights.pth

# train the CAVIS_Online
python train_net_video.py \
  --num-gpus 8 \
  --config-file /path/to/CAVIS_Online_config_file.yaml \
  --resume MODEL.WEIGHTS /path/to/segmenter_pretrained_weights.pth

# train the CAVIS_Offline
python train_net_video.py \
  --num-gpus 8 \
  --config-file /path/to/CAVIS_Offline_config_file.yaml \
  --resume MODEL.WEIGHTS /path/to/online_pretrained_weights.pth 
```

### Evaluation

Prepare the datasets following [datasets/README.md](./datasets/README.md) and download trained weights from [here](MODEL_ZOO.md).
Once these are set up, run:
```
python train_net_video.py \
  --num-gpus 8 \
  --config-file /path/to/config.yaml \
  --eval-only MODEL.WEIGHTS /path/to/weight.pth 
```


### Visualization

1. Pick a trained model and its config file. To start, you can pick from
  [model zoo](MODEL_ZOO.md),
  for example, `configs/ovis/CAVIS_Online_R50.yaml`.
2. We provide `demo_long_video.py` to visualize outputs of a trained model. Run it with:
```
python demo_long_video.py \
  --config-file /path/to/config.yaml \
  --input /path/to/images_folder \
  --output /path/to/output_folder \  
  --opts MODEL.WEIGHTS /path/to/checkpoint_file.pth

# if the video if long (> 300 frames), plese set the 'windows_size'
python demo_long_video.py \
  --config-file /path/to/config.yaml \
  --input /path/to/images_folder \
  --output /path/to/output_folder \  
  --windows_size 300 \
  --opts MODEL.WEIGHTS /path/to/checkpoint_file.pth
```
The input is a folder containing video frames saved as images. For example, `ytvis_2019/valid/JPEGImages/00f88c4f0a`.
