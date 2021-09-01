import typing

import torch
from torch.utils.data import Dataset, DataLoader

from src.dataclass import Context


@torch.jit.script
def get_sample(data: torch.Tensor, batch_index: torch.Tensor, idx: int) -> typing.Tuple[torch.Tensor, torch.Tensor]:
    dat = data[batch_index + idx]
    dat = dat.to(dtype=torch.long, non_blocking=True)
    return dat[:, :-1], dat[:, 1:]


class Dataset(Dataset):
    def __init__(self, ctx: Context):
        self.data = torch.load(ctx.dataset.file_name)
        batch_index = torch.arange(0, ctx.model.batch_size * ctx.optimizer.gradient_accumulation_steps).view(-1, 1)
        item_index = torch.arange(0, ctx.model.sequence_length + 1).view(1, -1)
        self.batch_index = batch_index + item_index
        self.length = self.data.size(0) // ctx.model.sequence_length // ctx.model.batch_size - 1
        self.batch_size = ctx.model.batch_size

    def __len__(self):
        return self.length

    def __getitem__(self, idx: int) -> typing.Tuple[torch.Tensor, torch.Tensor]:
        return get_sample(self.data, self.batch_index, idx * self.batch_size)


def get_dataset(ctx: Context) -> DataLoader:
    if ctx.dataset.prefetch_factor < ctx.dataset.shuffle:
        print(f"Warning: prefetch_factor ({ctx.dataset.prefetch_factor}) < num_workers ({ctx.dataset.num_workers})."
              f"Some workers will be idle at all times. Reducing num_workers ({ctx.dataset.num_workers}) to "
              f"prefetch_factor ({ctx.dataset.prefetch_factor}).")
    return DataLoader(Dataset(ctx), 1, ctx.dataset.shuffle,
                                       num_workers=min(ctx.dataset.num_workers, ctx.dataset.prefetch_factor),
                                       pin_memory=ctx.dataset.pin_memory, prefetch_factor=ctx.dataset.prefetch_factor)
