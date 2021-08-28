import math
import typing

import revlib
import torch
import torch.nn.functional
from src.dataclass import Context

QUAD_TENSOR = typing.Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]


@torch.jit.script
def _activate_norm(fn_input: torch.Tensor) -> torch.Tensor:
    out = torch.nn.functional.relu(fn_input)
    out = out - out.mean(-1, keepdim=True)
    return out / ((out.square().sum(-1, keepdim=True).sqrt() + 1e-5) * out.size(-1) ** -0.5)


@torch.jit.script
def conv(inp: torch.Tensor, weight: torch.Tensor) -> torch.Tensor:
    return torch.nn.functional.conv1d(torch.nn.functional.pad(inp, (weight.size()[-1] - 1, 0)), weight)


@torch.jit.script
def feed_forward(inp: torch.Tensor, w0: torch.Tensor, w1: torch.Tensor, w2: torch.Tensor) -> torch.Tensor:
    inp = conv(inp, w0)
    inp = _activate_norm(inp)
    inp = conv(inp, w1)
    inp = _activate_norm(inp)
    inp = conv(inp, w2)
    return inp


class FeedForward(torch.nn.Module):
    def __init__(self, ctx: Context, init_scale: float):
        super().__init__()
        intermediate = int(ctx.model.features * ctx.model.feed_forward_intermediate_factor)
        self.w0 = torch.nn.Conv1d(ctx.model.features, intermediate, (1,), bias=False).weight
        self.w1 = torch.nn.Conv1d(intermediate, intermediate, (ctx.model.conv_kernel_size,), bias=False).weight
        self.w2 = torch.nn.Conv1d(intermediate, ctx.model.features, (1,), bias=False).weight
        torch.nn.init.orthogonal_(self.w0.data, 1 / ctx.model.activation_std)
        torch.nn.init.orthogonal_(self.w1.data, 1 / ctx.model.activation_std)
        torch.nn.init.orthogonal_(self.w2.data, init_scale)

    def forward(self, inp: torch.Tensor):
        return feed_forward(inp, self.w0, self.w1, self.w2)


@torch.jit.script
def linear_attention(inp: torch.Tensor, depth: torch.Tensor, scale: torch.Tensor, shift: torch.Tensor,
                     divisor: torch.Tensor) -> torch.Tensor:
    return inp + _activate_norm(depth.cumsum(1) / divisor * scale + shift)


class LinearAttention(torch.nn.Module):
    """
    One idea would be to run linear attention at every step in an rnn
    """

    def __init__(self, ctx: Context):
        super(LinearAttention, self).__init__()
        self.embedding = torch.nn.Parameter(torch.randn((ctx.dataset.classes,
                                                         ctx.model.features * 2)).mul(ctx.model.input_embedding_std))
        init_scale = ctx.model.depth ** 0.5
        pos_embd = torch.arange(0, ctx.dataset.classes).unsqueeze(0) + 1
        feature_embd = torch.arange(0, ctx.model.features).unsqueeze(1) + 1
        additive = (feature_embd % 2).to(torch.float)
        feature_embd = (feature_embd - additive) / 2
        additive *= math.pi
        feature_embd *= 8 / ctx.model.features
        feature_embd -= math.log(ctx.dataset.classes / 2 / math.pi)
        feature_embd = torch.exp(feature_embd) + additive
        self.register_buffer("pos_embd", pos_embd)
        pos_embd = torch.sin(pos_embd * feature_embd).mul(ctx.model.position_embedding_std / init_scale).unsqueeze(0)
        self.register_buffer("divisor", pos_embd.unsqueeze(0).to(torch.float))
        self.stem = revlib.ReversibleSequential(*([LinearAttentionCell(self, ctx, init_scale)
                                                   for _ in range(ctx.model.device)] * ctx.model.weight_shared_blocks))
        self.output = torch.nn.Conv1d(ctx.model.features * 2, ctx.dataset.classes, (1,))

    def forward(self, inp: torch.Tensor, tgt: torch.Tensor):
        return torch.nn.functional.cross_entropy(self.output(self.stem(self.embedding[inp].transpose(1, 2))), tgt)


class LinearAttentionCell(torch.nn.Module):
    def __init__(self, base: LinearAttention, ctx: Context, init_scale: float):
        super(LinearAttentionCell, self).__init__()
        self.pos_embd = lambda: base.pos_embd
        self.divisor = lambda: base.divisor
        self.depth = FeedForward(ctx, 1)
        self.scale = FeedForward(ctx, init_scale / 2)
        self.shift = FeedForward(ctx, init_scale / 2)

    def forward(self, inp: torch.Tensor) -> torch.Tensor:
        out = inp + self.pos_embd()
        return linear_attention(inp, self.depth(out), self.scale(out), self.shift(out), self.divisor())
