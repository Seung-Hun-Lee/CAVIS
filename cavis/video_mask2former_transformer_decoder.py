
import torch
import torch.nn as nn
from torch.nn import functional as F
from detectron2.config import configurable
from mask2former.modeling.transformer_decoder.maskformer_transformer_decoder import TRANSFORMER_DECODER_REGISTRY
from mask2former.modeling.transformer_decoder.position_encoding import PositionEmbeddingSine
from mask2former_video.modeling.transformer_decoder.video_mask2former_transformer_decoder import VideoMultiScaleMaskedTransformerDecoder, MLP
import einops
import fvcore.nn.weight_init as weight_init


@TRANSFORMER_DECODER_REGISTRY.register()
class VideoMultiScaleMaskedTransformerDecoder_cavis(VideoMultiScaleMaskedTransformerDecoder):

    @configurable
    def __init__(
            self,
            in_channels,
            mask_classification=True,
            *,
            num_classes: int,
            hidden_dim: int,
            num_queries: int,
            nheads: int,
            dim_feedforward: int,
            dec_layers: int,
            pre_norm: bool,
            mask_dim: int,
            enforce_input_project: bool,
            # video related
            num_frames: int,
            # ct related
            num_reid_head_layers,
            reid_hidden_dim,
            # context fliter
            context_filter_size,
    ):
        super().__init__(
            in_channels=in_channels,
            mask_classification=mask_classification,
            num_classes=num_classes,
            hidden_dim=hidden_dim,
            num_queries=num_queries,
            nheads=nheads,
            dim_feedforward=dim_feedforward,
            dec_layers=dec_layers,
            pre_norm=pre_norm,
            mask_dim=mask_dim,
            enforce_input_project=enforce_input_project,
            num_frames=num_frames,
        )

        # use 2D positional embedding
        N_steps = hidden_dim // 2
        self.pe_layer = PositionEmbeddingSine(N_steps, normalize=True)

        # For contrastive learning
        if num_reid_head_layers > 0:
            self.reid_embed = MLP(
                hidden_dim*2, reid_hidden_dim, hidden_dim, num_reid_head_layers)
            for layer in self.reid_embed.layers:
                weight_init.c2_xavier_fill(layer)
        else:
            self.reid_embed = torch.nn.Identity()  # do nothing
            
        self.register_buffer("laplacian", torch.tensor(
            [-1, -1, -1, -1, 8, -1, -1, -1, -1],
            dtype=torch.float, requires_grad=False).reshape(1, 1, 3, 3).cuda())
        
        self.padding_size = (context_filter_size-1) // 2
        
        # We use average filter for to extract object surronding features (See Eq. (6) in Sec. 4.1.1)
        self.ctx_filter =  (1./(context_filter_size**2)) * torch.ones((context_filter_size, context_filter_size),
        dtype=torch.float, requires_grad=False).reshape(1, 1, context_filter_size, context_filter_size).repeat(hidden_dim, hidden_dim, 1, 1).cuda()
        
    @classmethod
    def from_config(cls, cfg, in_channels, mask_classification):
        ret = {}
        ret["in_channels"] = in_channels
        ret["mask_classification"] = mask_classification

        ret["num_classes"] = cfg.MODEL.SEM_SEG_HEAD.NUM_CLASSES
        ret["hidden_dim"] = cfg.MODEL.MASK_FORMER.HIDDEN_DIM
        ret["num_queries"] = cfg.MODEL.MASK_FORMER.NUM_OBJECT_QUERIES
        # Transformer parameters:
        ret["nheads"] = cfg.MODEL.MASK_FORMER.NHEADS
        ret["dim_feedforward"] = cfg.MODEL.MASK_FORMER.DIM_FEEDFORWARD

        # NOTE: because we add learnable query features which requires supervision,
        # we add minus 1 to decoder layers to be consistent with our loss
        # implementation: that is, number of auxiliary losses is always
        # equal to number of decoder layers. With learnable query features, the number of
        # auxiliary losses equals number of decoders plus 1.
        assert cfg.MODEL.MASK_FORMER.DEC_LAYERS >= 1
        ret["dec_layers"] = cfg.MODEL.MASK_FORMER.DEC_LAYERS - 1
        ret["pre_norm"] = cfg.MODEL.MASK_FORMER.PRE_NORM
        ret["enforce_input_project"] = cfg.MODEL.MASK_FORMER.ENFORCE_INPUT_PROJ

        ret["mask_dim"] = cfg.MODEL.SEM_SEG_HEAD.MASK_DIM

        ret["num_frames"] = cfg.INPUT.SAMPLING_FRAME_NUM

        ret["reid_hidden_dim"] = cfg.MODEL.MASK_FORMER.REID_HIDDEN_DIM
        ret["num_reid_head_layers"] = cfg.MODEL.MASK_FORMER.NUM_REID_HEAD_LAYERS
        ret["context_filter_size"] = cfg.MODEL.MASK_FORMER.CONTEXT_FILTER_SIZE
        return ret

    def forward(self, x, mask_features, mask=None):
        # x is a list of multi-scale feature  
        assert len(x) == self.num_feature_levels
        src = []
        pos = []
        size_list = []

        # disable mask, it does not affect performance
        del mask

        for i in range(self.num_feature_levels):
            size_list.append(x[i].shape[-2:])
            pos.append(self.pe_layer(x[i], None).flatten(2))
            src.append(self.input_proj[i](x[i]).flatten(2) + self.level_embed.weight[i][None, :, None])

            # flatten NxCxHxW to HWxNxC
            pos[-1] = pos[-1].permute(2, 0, 1)
            src[-1] = src[-1].permute(2, 0, 1)

        _, bs, _ = src[0].shape

        # QxNxC
        query_embed = self.query_embed.weight.unsqueeze(1).repeat(1, bs, 1)
        output = self.query_feat.weight.unsqueeze(1).repeat(1, bs, 1)

        predictions_class = []
        predictions_mask = []

        # prediction heads on learnable query features
        outputs_class, outputs_mask, attn_mask = self.forward_prediction_heads(
            output,
            mask_features,
            attn_mask_target_size=size_list[0]
        )
        predictions_class.append(outputs_class)
        predictions_mask.append(outputs_mask)

        for i in range(self.num_layers):
            level_index = i % self.num_feature_levels
            attn_mask[torch.where(attn_mask.sum(-1) == attn_mask.shape[-1])] = False
            # attention: cross-attention first
            output = self.transformer_cross_attention_layers[i](
                output, src[level_index],
                memory_mask=attn_mask,
                memory_key_padding_mask=None,  # here we do not apply masking on padded region
                pos=pos[level_index], query_pos=query_embed
            )

            output = self.transformer_self_attention_layers[i](
                output, tgt_mask=None,
                tgt_key_padding_mask=None,
                query_pos=query_embed
            )

            # FFN
            output = self.transformer_ffn_layers[i](
                output
            )

            outputs_class, outputs_mask, attn_mask = self.forward_prediction_heads(
                output,
                mask_features,
                attn_mask_target_size=size_list[(i + 1) % self.num_feature_levels]
            )
            predictions_class.append(outputs_class)
            predictions_mask.append(outputs_mask)

        assert len(predictions_class) == self.num_layers + 1
        
        ctx_embds = self.get_context_queries(predictions_mask[-1], mask_features)

        # expand BT to B, T
        bt = predictions_mask[-1].shape[0]
        bs = bt // self.num_frames if self.training else 1
        t = bt // bs
        for i in range(len(predictions_mask)):
            predictions_mask[i] = einops.rearrange(predictions_mask[i], '(b t) q h w -> b q t h w', t=t)

        for i in range(len(predictions_class)):
            predictions_class[i] = einops.rearrange(predictions_class[i], '(b t) q c -> b t q c', t=t)

        pred_embds_without_norm = einops.rearrange(output, 'q (b t) c -> b c t q', t=t)
        pred_embds = self.decoder_norm(output)
        reid_embed = self.reid_embed(torch.cat([pred_embds, ctx_embds], dim=-1))
        pred_embds = einops.rearrange(pred_embds, 'q (b t) c -> b c t q', t=t)
        reid_embed = einops.rearrange(reid_embed, 'q (b t) c -> b c t q', t=t)

        out = {
            'pred_logits': predictions_class[-1],
            'pred_masks': predictions_mask[-1],
            'aux_outputs': self._set_aux_loss(
                predictions_class if self.mask_classification else None, predictions_mask
            ),
            # 'pred_embds': pred_embds,
            'pred_embds_without_norm': pred_embds_without_norm,
            'pred_embds': torch.cat([pred_embds, reid_embed], dim=1),
            # 'pred_embds_without_norm': torch.cat([pred_embds_without_norm, reid_embed], dim=1),
            'pred_reid_embed': reid_embed,
            'mask_features': mask_features
        }
        return out

    def forward_prediction_heads(self, output, mask_features, attn_mask_target_size):
        decoder_output = self.decoder_norm(output)
        decoder_output = decoder_output.transpose(0, 1)
        outputs_class = self.class_embed(decoder_output)
        mask_embed = self.mask_embed(decoder_output)
        outputs_mask = torch.einsum("bqc,bchw->bqhw", mask_embed, mask_features)

        # NOTE: prediction is of higher-resolution
        # [B, Q, H, W] -> [B, Q, H*W] -> [B, h, Q, H*W] -> [B*h, Q, HW]
        attn_mask = F.interpolate(outputs_mask, size=attn_mask_target_size, mode="bilinear", align_corners=False)
        # must use bool type
        # If a BoolTensor is provided, positions with ``True`` are not allowed to attend while ``False`` values will be unchanged.
        attn_mask = (attn_mask.sigmoid().flatten(2).unsqueeze(1).repeat(1, self.num_heads, 1, 1).flatten(0,
                                                                                                         1) < 0.5).bool()
        attn_mask = attn_mask.detach()

        return outputs_class, outputs_mask, attn_mask
    
    # See Eq. (6) in Sec. 4.1.1
    def get_context_queries(self, pred_masks, mask_features):
        # pred_masks: B, Q, H, W
        # mask_features: B, C, H, W
        B, Q, h, w = pred_masks.shape
        mask = pred_masks.clone().detach()
        mask = (mask>0).float().flatten(0, 1)
        bdy = F.conv2d(mask.unsqueeze(1), self.laplacian, padding=1).squeeze(1).reshape(B, Q, h, w)
        bdy = (bdy>0).float().flatten(2)
        
        x = F.conv2d(mask_features, self.ctx_filter, padding=self.padding_size).flatten(2)
        num_points = bdy.sum(-1).unsqueeze(-1) + 1e-6  
        ctx = torch.einsum('bqs, bcs->bqc', bdy, x) / num_points
        return ctx.permute(1, 0, 2)
    
