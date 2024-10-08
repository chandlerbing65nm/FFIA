#!/usr/bin/env python

"""Module docstring goes here."""

from __future__ import annotations

from math import pi
from typing import Any, List, Optional, Tuple

import matplotlib.pyplot as plt
import torch
from torch import nn


class DSTFT(nn.Module):
    """Differentiable short-time Fourier transform (DSTFT) module.

    Args:
    ----
        nn (_type_): _description_

    """

    def __init__(
        self: DSTFT,
        x: torch.tensor,
        win_length: float,
        support: int,
        stride: int,
        pow: float = 1.0,
        win_pow: float = 1.0,
        win_p: str | None = None,
        stride_p: str | None = None,
        pow_p: str | None = None,
        win_requires_grad=True,
        stride_requires_grad: bool = True,
        pow_requires_grad: bool = False,
        # params: str = 'p_tf', # p, p_t, p_f, p_tf
        win_min: float | None = None,
        win_max: float | None = None,
        stride_min: float | None = None,
        stride_max: float | None = None,
        pow_min: float | None = None,
        pow_max: float | None = None,
        tapering_function: str = "hann",
        sr: int = 16_000,
        window_transform=None,
        stride_transform=None,
        dynamic_parameter: bool = False,
        first_frame: bool = False,
    ):
        super().__init__()

        # Constants and hyperparameters
        self.N = support  # support size
        self.F = int(1 + self.N / 2)  # nb of frequencies
        self.B = x.shape[0]  # batch size
        self.L = x.shape[-1]  # signal length
        self.device = x.device
        self.dtype = x.dtype

        self.win_requires_grad = win_requires_grad
        self.stride_requires_grad = stride_requires_grad
        self.pow_requires_grad = pow_requires_grad
        self.tapering_function = tapering_function
        self.dynamic_parameter = dynamic_parameter
        self.first_frame = first_frame
        self.sr = sr
        self.pow = pow
        self.tap_win = None

        # Register eps and min as a buffer tensor
        self.register_buffer(
            "eps",
            torch.tensor(
                torch.finfo(torch.float).eps,
                dtype=self.dtype,
                device=self.device,
            ),
        )
        self.register_buffer(
            "min",
            torch.tensor(
                torch.finfo(torch.float).min,
                dtype=self.dtype,
                device=self.device,
            ),
        )

        # Calculate the number of frames
        # self.T = int(
        #    1
        #    + torch.div(
        #        x.shape[-1] - (self.N - 1) - 1, stride, rounding_mode="floor",
        #    ),
        # )
        self.T = 1 + int(torch.div(x.shape[-1], stride, rounding_mode="floor"))

        if win_min is None:
            self.win_min = self.N / 20
        else:
            self.win_min = win_min
        if win_max is None:
            self.win_max = self.N
        else:
            self.win_max = win_max
        if stride_min is None:
            self.stride_min = 0
        else:
            self.stride_min = stride_min
        if stride_max is None:
            self.stride_max = max(self.N, abs(stride))
        else:
            self.stride_max = stride_max
        if pow_min is None:
            self.pow_min = 0.001
        else:
            self.pow_min = pow_min
        if pow_max is None:
            self.pow_max = 1000
        else:
            self.pow_max = pow_max

        # HOP LENGTH / FRAME INDEX
        # hop length constraints
        if stride_transform is None:
            self.stride_transform = self.__stride_transform
        else:
            self.stride_transform = stride_transform

        # Determine the shape of the stride/hop-length/ frame index parameters
        if stride_p is None:
            stride_size = (1,)
        elif stride_p == "t":
            stride_size = (self.T,)
        else:
            raise ValueError(f"stride_p error {stride_p}")
        # Create the window length parameter and assign it the appropriate shape
        self.init_stride = abs(stride)
        self.strides = nn.Parameter(
            torch.full(
                stride_size,
                self.init_stride,
                dtype=self.dtype,
                device=self.device,
            ),
            requires_grad=self.stride_requires_grad,
        )

        # WIN LENGTH
        # win length constraints
        if window_transform is None:
            self.window_transform = self.__window_transform
        else:
            self.window_transform = window_transform

        # Determine the shape of the window length parameters
        if win_p is None:
            win_length_size = (1, 1)
        elif win_p == "t":
            win_length_size = (1, self.T)
        else:
            raise ValueError(
                f"win_p error {win_p}, maybe use ADSTFT instead (frequency varying windows)",
            )
        # Create the window length parameter and assign it the appropriate shape
        self.win_length = nn.Parameter(
            torch.full(
                win_length_size,
                abs(win_length),
                dtype=self.dtype,
                device=self.device,
            ),
            requires_grad=self.win_requires_grad,
        )

        # WIN POW
        if pow_p is None:
            win_pow_size = (1, 1)
        elif pow_p == "t":
            win_pow_size = (1, self.T)
        else:
            print("pow_p error", pow_p)
        self.win_pow = nn.Parameter(
            torch.full(
                win_pow_size,
                abs(win_pow),
                dtype=self.dtype,
                device=self.device,
            ),
            requires_grad=self.pow_requires_grad,
        )

    def __window_transform(self: DSTFT, w_in):
        w_out = torch.minimum(
            torch.maximum(
                w_in,
                torch.full_like(
                    w_in, self.win_min, dtype=self.dtype, device=self.device,
                ),
            ),
            torch.full_like(
                w_in, self.win_max, dtype=self.dtype, device=self.device,
            ),
        )
        return w_out

    def __stride_transform(
        self: DSTFT, s_in: torch.Tensor,
    ):  # born stride entre 0 et 2N
        s_out = torch.minimum(
            torch.maximum(
                s_in,
                torch.full_like(
                    s_in, self.stride_min, dtype=self.dtype, device=self.device,
                ),
            ),
            torch.full_like(
                s_in, self.stride_max, dtype=self.dtype, device=self.device,
            ),
        )
        return s_out

    def __pow_transform(
        self: DSTFT, p_in: torch.Tensor,
    ):  # born stride entre 0 et 2N
        p_out = torch.minimum(
            torch.maximum(
                p_in,
                torch.full_like(
                    p_in, self.pow_min, dtype=self.dtype, device=self.device,
                ),
            ),
            torch.full_like(
                p_in, self.pow_max, dtype=self.dtype, device=self.device,
            ),
        )
        return p_out

    @property
    def actual_win_length(self: DSTFT):  # contraints
        return self.window_transform(self.win_length)

    @property
    def actual_strides(
        self,
    ):  # stride contraints, actual stride between frames
        return self.stride_transform(self.strides)

    @property
    def actual_pow(self: DSTFT):  # pow contraints
        return self.pow_transform(self.win_pow)

    @property
    def frames(self: DSTFT):
        # Compute the temporal position (indices) of frames (support)
        expanded_stride = self.actual_strides.expand((self.T,))
        frames = torch.zeros_like(expanded_stride)
        # frames[0] = - self.N / 2
        # if self.first_frame:
        #    frames[0] = (
        #        self.actual_win_length.expand((self.N, self.T))[:, 0].max(
        #            dim=0, keepdim=False,
        #        )[0]
        #        - self.N
        #    ) / 2
        frames -= self.N / 2 + self.init_stride
        frames += expanded_stride.cumsum(dim=0)

        return frames

    @property
    def effective_strides(self: DSTFT):
        # Compute the strides between window (and not frames)
        expanded_stride = self.actual_strides.expand((self.T,))
        effective_strides = torch.zeros_like(expanded_stride)
        effective_strides[1:] = expanded_stride[1:]
        cat = (
            torch.cat(
                (
                    torch.tensor(
                        [self.N], dtype=self.dtype, device=self.device,
                    ),
                    self.actual_win_length.expand((self.N, self.T)).max(
                        dim=0, keepdim=False,
                    )[0],
                ),
                dim=0,
            ).diff()
            / 2
        )
        effective_strides = effective_strides - cat
        return effective_strides

    def forward(self: DSTFT, x: torch.tensor) -> tuple:
        # Perform the forward STFT and extract the magnitude, phase, real, and imaginary parts
        stft = self.stft(x, "forward")
        spec = stft.abs().pow(self.pow)[:, : self.F] + torch.finfo(x.dtype).eps
        return spec, stft  # , real, imag, phase

    def backward(
        self: DSTFT, x: torch.tensor, dl_ds: torch.tensor,
    ) -> torch.tensor:
        # Compute the gradient of the loss w.r.t. window length parameter with the chain rule
        dstft_dp = self.stft(x, "backward")
        dl_dp = torch.conj(dl_ds) * dstft_dp
        dl_dp = dl_dp.sum().real.expand(self.win_length.shape)
        return dl_dp

    def stft(self: DSTFT, x: torch.tensor, direction: str):
        # batch_size, length, device, dtype = x.shape[0], x.shape[-1], x.device, x.dtype

        # Generate strided signal and shift idx_frac
        folded_x, idx_frac = self.unfold(x)  # B, T, N; T

        # Generate the tapering window function for the STFT
        self.tap_win = self.window_function(
            direction=direction, idx_frac=idx_frac,
        ).permute(1, 0)  # T, N

        # Compute tapered x
        self.folded_x = folded_x[:, :, :]  # B, T, N
        self.tap_win = self.tap_win[None, :, :]  # 1, T, 1
        self.tapered_x = self.folded_x * self.tap_win  # B, T, N,

        spectr = torch.fft.rfft(self.tapered_x)

        shift = torch.arange(
            end=self.F,
            device=self.device,
            dtype=self.dtype,
            requires_grad=False,
        )
        shift = idx_frac[:, None] * shift[None, :]  # T, N
        shift = torch.exp(2j * pi * shift / self.N)[None, ...]  # 1, T, N

        stft = spectr * shift
        return stft.permute(0, 2, 1)

    def inverse_dstft(self: DSTFT, stft: torch.Tensor) -> torch.tensor:
        """Compute inverse differentiable short-time Fourier transform (IDSTFT).

        Args:
        ----
            self (DSTFT): _description_
            stft (torch.Tensor): _description_

        Returns:
        -------
            torch.tensor: _description_

        """
        # shift
        # shift = torch.arange(
        #     end=self.F,
        #     device=self.device,
        #     dtype=self.dtype,
        #     requires_grad=False,
        # )
        # shift = idx_frac[:, None] * shift[None, :]  # T, N
        # stft = stft * torch.exp(2j * pi * shift / self.N)[None, ...]  # 1, T, N
        # print(stft.shape)

        # inverse
        # print(stft.shape, stft.dtype)
        ifft = torch.fft.irfft(stft, n=self.N, dim=-2)
        # print(ifft.shape, self.tap_win.sum(-1, keepdim=True).shape)
        
        # add shift
        self.itap_win = self.synt_win(None, None)
        ifft = ifft.permute(0, -1, -2) * self.itap_win
        
        # fold
        x_hat = self.fold(ifft)

        return x_hat

    def unfold(self: DSTFT, x: torch.tensor) -> torch.tensor:
        # frames index and strided x
        idx_floor = self.frames.floor()
        # print(self.frames.shape, self.frames)
        idx_frac = self.frames - idx_floor
        idx_floor = idx_floor.long()[:, None].expand((
            self.T,
            self.N,
        )) + torch.arange(0, self.N, device=self.device)
        idx_floor[idx_floor >= self.L] = -1
        # print(self.B, idx_floor.shape, x.shape)
        folded_x = x[:, idx_floor]
        folded_x[:, idx_floor < 0] = 0
        return folded_x, idx_frac

    def fold(self: DSTFT, folded_x: torch.tensor) -> torch.tensor:
        x_hat = torch.zeros(
            self.B, self.L, device=self.device, dtype=self.dtype,
        )
        # print(x_hat.shape, self.B, self.L)
        #print(folded_x.shape)
        for t in range(self.T):
            start_idx = max(0, int(self.frames[t]))
            end_idx = min(self.L - 1, int(self.frames[t]) + self.N)
            start_dec = start_idx - int(self.frames[t])
            end_dec = end_idx - int(self.frames[t])
            x_hat[:, start_idx:end_idx] += folded_x[:, t, start_dec:end_dec]
        return x_hat

    def window_function(self: DSTFT, direction: str, idx_frac) -> torch.tensor:
        if self.tapering_function not in {"hann", "hanning"}:
            raise ValueError(
                f"tapering_function must be one of '{('hann', 'hanning')}', but got padding_mode='{self.tapering_function}'",
            )
        else:
            # Create an array of indices to use as the base for the window function
            base = torch.arange(
                0, self.N, 1, dtype=self.dtype, device=self.device,
            )[:, None].expand([-1, self.T])
            base = base - idx_frac
            # Expand the win_length parameter to match the shape of the base array

        # calculate the tapering function and its derivate w.r.t. window length
        mask1 = base.ge(torch.ceil((self.N - 1 + self.actual_win_length) / 2))
        mask2 = base.le(torch.floor((self.N - 1 - self.actual_win_length) / 2))
        if (
            self.tapering_function == "hann"
            or self.tapering_function == "hanning"
        ):
            if direction == "forward":
                self.tap_win = 0.5 - 0.5 * torch.cos(
                    2
                    * pi
                    * (base + (self.actual_win_length - self.N + 1) / 2)
                    / self.actual_win_length,
                )
                self.tap_win[mask1] = 0
                self.tap_win[mask2] = 0
                # self.tap_win = self.tap_win / self.tap_win.sum(
                #    dim=0, keepdim=True,
                # )
                return self.tap_win.pow(self.win_pow)

            elif direction == "backward":
                f = torch.sin(
                    2
                    * pi
                    * (base + (self.actual_win_length - self.N + 1) / 2)
                    / self.actual_win_length,
                )
                d_tap_win = (
                    -pi
                    / self.actual_win_length.pow(2)
                    * ((self.N - 1) / 2 - base)
                    * f
                )
                d_tap_win[mask1] = 0
                d_tap_win[mask2] = 0
                d_tap_win = d_tap_win / self.N * 2
                return d_tap_win
        return None

    def synt_win(self: DSTFT, direction: str, idx_frac) -> torch.tensor:
        
        wins = torch.zeros(self.L)        
        for t in range(self.T):
            start_idx = max(0, int(self.frames[t]))
            end_idx = min(self.L, int(self.frames[t]) + self.N)
            start_dec = start_idx - int(self.frames[t])
            end_dec = end_idx - int(self.frames[t])
            wins[start_idx:end_idx] += (
                self.tap_win[:, t, start_dec:end_dec].squeeze().detach().cpu()
            )
        self.wins = wins
        self.iwins = torch.zeros(self.L)
        self.iwins[ self.wins > 0 ] = 1 / self.wins[self.wins > 0]
        
        plt.plot(self.wins, label="wins")
        plt.plot(self.iwins, label='iwins')
        plt.plot(self.iwins * self.wins)
        plt.legend()
        
        itap_win = torch.zeros_like(self.tap_win)
        for t in range(self.T):
            start_idx = max(0, int(self.frames[t]))
            end_idx = min(self.L, int(self.frames[t]) + self.N)
            start_dec = start_idx - int(self.frames[t])
            end_dec = end_idx - int(self.frames[t])
            itap_win[:, t, start_dec:end_dec] = (
            #self.tap_win[:, t, start_dec:end_dec]
            #/ wins[start_idx:end_idx] * 
            self.iwins[start_idx:end_idx]
        )
        return itap_win

    def coverage(self: DSTFT):  # in [0, 1]
        # compute coverage
        expanded_win, _ = self.actual_win_length.expand((self.N, self.T)).min(
            dim=0, keepdim=False,
        )
        cov = expanded_win[0]
        maxi = self.frames[0] + self.N / 2 + expanded_win[0] / 2
        for i in range(1, self.T):
            start = torch.min(
                self.L * torch.ones_like(expanded_win[i]),
                torch.max(
                    torch.zeros_like(expanded_win[i]),
                    self.frames[i] + self.N / 2 - expanded_win[i] / 2,
                ),
            )
            end = torch.min(
                self.L * torch.ones_like(expanded_win[i]),
                torch.max(
                    torch.zeros_like(expanded_win[i]),
                    self.frames[i] + self.N / 2 + expanded_win[i] / 2,
                ),
            )
            if end > maxi:
                cov += end - torch.max(start, maxi)
                maxi = end
        cov /= self.L
        return cov

    def plot(
        self: DSTFT,
        spec: torch.Tensor,
        x: torch.Tensor | None = None,
        marklist: Optional[List[Any]] = None,
        figsize=(6.4, 4.8),
        f_hat=None,
        fs=None,
        *,
        weights: bool = True,
        wins: bool = True,
        bar: bool = False,
        cmap: float = "jet",
        ylabel: float = "frequencies",
        xlabel: float = "frames",
    ):
        f_max = spec.shape[-2] if fs is None else fs / 2
        plt.figure(figsize=figsize)
        plt.title("Spectrogram")
        ax = plt.subplot()
        im = ax.imshow(
            spec[0].detach().cpu().log(),
            aspect="auto",
            origin="lower",
            cmap=cmap,
            extent=[0, spec.shape[-1], 0, f_max],
        )
        plt.ylabel(ylabel)
        plt.xlabel(xlabel)
        if bar == True:
            plt.colorbar(im, ax=ax)
        if f_hat is not None:
            for f in f_hat:
                plt.plot(f, linewidth=0.5, c="k", alpha=0.7)
        plt.show()

        if weights == True:
            plt.figure(figsize=figsize)
            plt.title("Distribution of window lengths")
            ax = plt.subplot()
            im = ax.imshow(
                self.actual_win_length[: self.F].detach().cpu(),
                aspect="auto",
                origin="lower",
                cmap=cmap,
            )
            ax.set_ylabel(ylabel)
            ax.set_xlabel(xlabel)
            if bar == True:
                plt.colorbar(im, ax=ax)
                im.set_clim(self.win_min, self.win_max)
            plt.show()

        if self.tap_win is not None and wins == True:
            fig, ax = plt.subplots(figsize=figsize)
            ax.plot(self.T + 0.5 + x.squeeze().cpu().numpy(), linewidth=1)
            for i, start in enumerate(self.frames.detach().cpu()):
                ax.plot(
                    range(
                        int(start.floor().item()),
                        int(start.floor().item() + self.N),
                    ),
                    self.T
                    - i
                    - 1.3
                    + self.tap_win[:, i, :].squeeze().detach().cpu(),
                    c="#1f77b4",
                )

            if marklist is not None:
                for elem in marklist:
                    plt.axvline(elem, 0, self.T, c="gray")
            else:
                ax.axvline(x=0, ymin=0, ymax=self.T, c="gray")
                ax.axvline(x=x.shape[-1], ymin=0, ymax=self.T, c="gray")
            plt.show()


class ADSTFT(nn.Module):
    """Adaptive differentiable short-time Fourier transform (ADSTFT) module.

    Args:
    ----
        nn (_type_): _description_

    """

    def __init__(
        self: ADSTFT,
        x: torch.tensor,
        win_length: float,
        support: int,
        stride: int,
        pow: float = 1.0,
        win_pow: float = 1.0,
        win_p: str | None = None,
        stride_p: str | None = None,
        pow_p: str | None = None,
        win_requires_grad=True,
        stride_requires_grad: bool = True,
        pow_requires_grad: bool = False,
        # params: str = 'p_tf', # p, p_t, p_f, p_tf
        win_min: float | None = None,
        win_max: float | None = None,
        stride_min: float | None = None,
        stride_max: float | None = None,
        pow_min: float | None = None,
        pow_max: float | None = None,
        tapering_function: str = "hann",
        sr: int = 16_000,
        window_transform=None,
        stride_transform=None,
        dynamic_parameter: bool = False,
        first_frame: bool = False,
    ):
        super().__init__()

        # Constants and hyperparameters
        self.N = support  # support size
        self.F = int(1 + self.N / 2)  # nb of frequencies
        self.B = x.shape[0]  # batch size
        self.L = x.shape[-1]  # signal length
        self.device = x.device
        self.dtype = x.dtype

        self.win_requires_grad = win_requires_grad
        self.stride_requires_grad = stride_requires_grad
        self.pow_requires_grad = pow_requires_grad
        self.tapering_function = tapering_function
        self.dynamic_parameter = dynamic_parameter
        self.first_frame = first_frame
        self.sr = sr
        self.pow = pow
        self.tap_win = None

        # Register eps and min as a buffer tensor
        self.register_buffer(
            "eps",
            torch.tensor(
                torch.finfo(torch.float).eps,
                dtype=self.dtype,
                device=self.device,
            ),
        )
        self.register_buffer(
            "min",
            torch.tensor(
                torch.finfo(torch.float).min,
                dtype=self.dtype,
                device=self.device,
            ),
        )

        # Calculate the number of frames
        self.T = int(
            1
            + torch.div(
                x.shape[-1] - (self.N - 1) - 1, stride, rounding_mode="floor",
            ),
        )

        # self.T = int(
        #     1
        #     + (x.shape[-1] - (self.N - 1) - 1 + stride - 1) // stride
        # )


        if win_min is None:
            self.win_min = self.N / 20
        else:
            self.win_min = win_min
        if win_max is None:
            self.win_max = self.N
        else:
            self.win_max = win_max
        if stride_min is None:
            self.stride_min = 0
        else:
            self.stride_min = stride_min
        if stride_max is None:
            self.stride_max = max(self.N, abs(stride))
        else:
            self.stride_max = stride_max
        if pow_min is None:
            self.pow_min = 0.001
        else:
            self.pow_min = pow_min
        if pow_max is None:
            self.pow_max = 1000
        else:
            self.pow_max = pow_max

        # HOP LENGTH / FRAME INDEX
        if stride_transform is None:
            self.stride_transform = self.__stride_transform
        else:
            self.stride_transform = stride_transform
        # Determine the shape of the stride/hop-length/ frame index parameters
        if stride_p is None:
            stride_size = (1,)
        elif stride_p == "t":
            stride_size = (self.T,)
        else:
            raise ValueError(f"stride_p error {stride_p}")
        # Create the window length parameter and assign it the appropriate shape
        self.strides = nn.Parameter(
            torch.full(
                stride_size, abs(stride), dtype=self.dtype, device=self.device,
            ),
            requires_grad=self.stride_requires_grad,
        )

        # WIN LENGTH
        # win length constraints
        if window_transform is None:
            self.window_transform = self.__window_transform
        else:
            self.window_transform = window_transform
        # Determine the shape of the window length parameters
        if win_p is None:
            win_length_size = (1, 1)
        elif win_p == "t":
            win_length_size = (1, self.T)
        elif win_p == "f":
            win_length_size = (self.F, 1)
        elif win_p == "tf":
            win_length_size = (self.F, self.T)
        else:
            raise ValueError(f"win_p error {win_p}")
        # Create the window length parameter and assign it the appropriate shape
        self.win_length = nn.Parameter(
            torch.full(
                win_length_size,
                abs(win_length),
                dtype=self.dtype,
                device=self.device,
            ),
            requires_grad=self.win_requires_grad,
        )

        # WIN POW
        if pow_p is None:
            win_pow_size = (1, 1)
        elif pow_p == "t":
            win_pow_size = (1, self.T)
        elif pow_p == "f":
            win_pow_size = (self.F, 1)
        elif pow_p == "tf":
            win_pow_size = (self.F, self.T)
        else:
            print("pow_p error", pow_p)
        self.win_pow = nn.Parameter(
            torch.full(
                win_pow_size,
                abs(win_pow),
                dtype=self.dtype,
                device=self.device,
            ),
            requires_grad=self.pow_requires_grad,
        )

    def __window_transform(self: ADSTFT, w_in):
        """_summary_

        Args:
        ----
            w_in (_type_): _description_

        Returns:
        -------
            _type_: _description_

        """
        w_out = torch.minimum(
            torch.maximum(
                w_in,
                torch.full_like(
                    w_in, self.win_min, dtype=self.dtype, device=self.device,
                ),
            ),
            torch.full_like(
                w_in, self.win_max, dtype=self.dtype, device=self.device,
            ),
        )
        return w_out

    def __stride_transform(self: ADSTFT, s_in):  # born stride entre 0 et 2N
        s_out = torch.minimum(
            torch.maximum(
                s_in,
                torch.full_like(
                    s_in, self.stride_min, dtype=self.dtype, device=self.device,
                ),
            ),
            torch.full_like(
                s_in, self.stride_max, dtype=self.dtype, device=self.device,
            ),
        )
        return s_out

    def __pow_transform(self: ADSTFT, p_in):  # born stride entre 0 et 2N
        p_out = torch.minimum(
            torch.maximum(
                p_in,
                torch.full_like(
                    p_in, self.pow_min, dtype=self.dtype, device=self.device,
                ),
            ),
            torch.full_like(
                p_in, self.pow_max, dtype=self.dtype, device=self.device,
            ),
        )
        return p_out

    @property
    def actual_win_length(self: ADSTFT):  # contraints
        return self.window_transform(self.win_length)

    @property
    def actual_strides(
        self,
    ):  # stride contraints, actual stride between frames
        return self.stride_transform(self.strides)

    @property
    def actual_pow(self: ADSTFT):  # pow contraints
        return self.pow_transform(self.win_pow)

    @property
    def frames(self: ADSTFT):
        # Compute the temporal position (indices) of frames (support)
        expanded_stride = self.actual_strides.expand((self.T,))
        frames = torch.zeros_like(expanded_stride)
        if self.first_frame:
            frames[0] = (
                self.actual_win_length.expand((self.N, self.T))[:, 0].max(
                    dim=0, keepdim=False,
                )[0]
                - self.N
            ) / 2
        frames[1:] = frames[0] + expanded_stride[1:].cumsum(dim=0)
        return frames

    @property
    def effective_strides(self: ADSTFT):
        # Compute the strides between window (and not frames)
        expanded_stride = self.actual_strides.expand((self.T,))
        effective_strides = torch.zeros_like(expanded_stride)
        effective_strides[1:] = expanded_stride[1:]
        cat = (
            torch.cat(
                (
                    torch.tensor(
                        [self.N], dtype=self.dtype, device=self.device,
                    ),
                    self.actual_win_length.expand((self.N, self.T)).max(
                        dim=0, keepdim=False,
                    )[0],
                ),
                dim=0,
            ).diff()
            / 2
        )
        effective_strides = effective_strides - cat
        return effective_strides

    def forward(self: ADSTFT, x: torch.tensor) -> tuple:
        # Perform the forward STFT and extract the magnitude, phase, real, and imaginary parts
        stft = self.stft(x, "forward")
        spec = stft.abs().pow(self.pow)[:, : self.F] + torch.finfo(x.dtype).eps
        return spec, stft

    def backward(
        self: ADSTFT, x: torch.tensor, dl_ds: torch.tensor,
    ) -> torch.tensor:
        # Compute the gradient of the loss w.r.t. window length parameter with the chain rule
        dstft_dp = self.stft(x, "backward")
        dl_dp = torch.conj(dl_ds) * dstft_dp
        dl_dp = dl_dp.sum().real.expand(self.win_length.shape)
        return dl_dp

    def stft(self: ADSTFT, x: torch.tensor, direction: str):
        # batch_size, length, device, dtype = x.shape[0], x.shape[-1], x.device, x.dtype

        # Generate strided signal and shift idx_frac
        folded_x, idx_frac = self.unfold(x)  # B, T, N; T

        # Generate the tapering window function for the STFT
        self.tap_win = self.window_function(
            direction=direction, idx_frac=idx_frac,
        ).permute(2, 1, 0)  # T, N, N

        # Generate tapering function shift
        shift = torch.arange(
            end=self.F,
            device=self.device,
            dtype=self.dtype,
            requires_grad=False,
        )
        shift = idx_frac[:, None] * shift[None, :]  # T, N

        # Compute tapered x
        self.folded_x = folded_x[:, :, None, :]  # B, T, 1, N
        self.tap_win = self.tap_win[None, :, :, :]  # 1, T, F, 1
        shift = torch.exp(2j * pi * shift / self.N)[
            None, :, :, None,
        ]  # 1, T, N, 1
        self.tapered_x = self.folded_x * self.tap_win * shift  # B, T, F, N

        # Generate Fourier coefficients
        coeff = torch.arange(
            end=self.N,
            device=self.device,
            dtype=self.dtype,
            requires_grad=False,
        )
        # coeff = coeff[:, None] @ coeff[None, :]
        coeff = coeff[: self.F, None] @ coeff[None, :]
        coeff = torch.exp(-2j * pi * coeff / self.N)  # N, N

        # Perform the STFT
        coeff = coeff[None, None, :, :]  # 1, 1, N, N
        stft = (self.tapered_x * coeff).sum(dim=-1)
        return stft.permute(0, 2, 1)

    def unfold(self: ADSTFT, x) -> torch.tensor:
        # frames index and strided x
        idx_floor = self.frames.floor()
        idx_frac = self.frames - idx_floor
        idx_floor = idx_floor.long()[:, None].expand((
            self.T,
            self.N,
        )) + torch.arange(0, self.N, device=self.device)
        idx_floor[idx_floor >= self.L] = -1
        folded_x = x[:, idx_floor]
        folded_x[:, idx_floor < 0] = 0
        return folded_x, idx_frac


    def window_function(
        self: ADSTFT, direction: str, idx_frac,
    ) -> torch.tensor:
        if self.tapering_function not in {"hann", "hanning"}:
            raise ValueError(
                f"tapering_function must be one of '{('hann', 'hanning')}', but got padding_mode='{self.tapering_function}'",
            )
        else:
            # Create an array of indices to use as the base for the window function
            base = torch.arange(
                0, self.N, 1, dtype=self.dtype, device=self.device,
            )[:, None, None].expand([-1, self.F, self.T])
            base = base - idx_frac
            # Expand the win_length parameter to match the shape of the base array
            # if self.actual_win_length.dim() == 3:
            #    self.expanded_win_length = self.actual_win_length.expand([self.N, self.N, self.T])
            # elif self.actual_win_length.dim() == 1:
            #    self.expanded_win_length = self.actual_win_length[:, None, None].expand([self.N, self.N, self.T])
            # elif self.actual_win_length.dim() == 2 and self.actual_win_length.shape[-1] == self.T:
            #    self.expanded_win_length = self.actual_win_length[:, None, :].expand([self.N, self.N, self.T])
            # elif self.actual_win_length.dim() == 2 and self.actual_win_length.shape[-1] == self.N:
            #    self.expanded_win_length = self.actual_win_length[:, :, None].expand([self.N, self.N, self.T])

        mask1 = base.ge(torch.ceil((self.N - 1 + self.actual_win_length) / 2))
        mask2 = base.le(torch.floor((self.N - 1 - self.actual_win_length) / 2))

        # calculate the tapering function and its derivate w.r.t. window length
        if (
            self.tapering_function == "hann"
            or self.tapering_function == "hanning"
        ):
            if direction == "forward":
                self.tap_win = 0.5 - 0.5 * torch.cos(
                    2
                    * pi
                    * (base + (self.actual_win_length - self.N + 1) / 2)
                    / self.actual_win_length,
                )
                # mask1 = base.ge(torch.ceil( (self.N-1+self.actual_win_length)/2))
                # mask2 = base.le(torch.floor((self.N-1-self.actual_win_length)/2))
                self.tap_win[mask1] = 0
                self.tap_win[mask2] = 0
                # self.tap_win = self.tap_win.pow(self.actual_pow)
                self.tap_win = self.tap_win / self.N * 2
                return self.tap_win

            elif direction == "backward":
                # f = torch.sin(2 * pi * (base - (self.N-1)/2) /
                #             self.actual_win_length)
                f = torch.sin(
                    2
                    * pi
                    * (base + (self.actual_win_length - self.N + 1) / 2)
                    / self.actual_win_length,
                )
                d_tap_win = (
                    -pi
                    / self.actual_win_length.pow(2)
                    * ((self.N - 1) / 2 - base)
                    * f
                )
                d_tap_win[mask1] = 0
                d_tap_win[mask2] = 0
                d_tap_win = d_tap_win / self.N * 2
                return d_tap_win
        return None

    def coverage(self: ADSTFT):  # in [0, 1]
        # compute coverage
        expanded_win, _ = self.actual_win_length.expand((self.N, self.T)).min(
            dim=0, keepdim=False,
        )
        cov = expanded_win[0]
        maxi = self.frames[0] + self.N / 2 + expanded_win[0] / 2
        for i in range(1, self.T):
            start = torch.min(
                self.L * torch.ones_like(expanded_win[i]),
                torch.max(
                    torch.zeros_like(expanded_win[i]),
                    self.frames[i] + self.N / 2 - expanded_win[i] / 2,
                ),
            )
            end = torch.min(
                self.L * torch.ones_like(expanded_win[i]),
                torch.max(
                    torch.zeros_like(expanded_win[i]),
                    self.frames[i] + self.N / 2 + expanded_win[i] / 2,
                ),
            )
            if end > maxi:
                cov += end - torch.max(start, maxi)
                maxi = end
        cov /= self.L
        return cov

    def plot(
        self: ADSTFT,
        spec: torch.tensor,
        x: Optional[torch.tensor] = None,
        marklist: Optional[List[int]] = None,
        bar: bool = False,
        figsize: Tuple[float, float] = (6.4, 4.8),
        f_hat=None,
        fs=None,
        *,
        weights: bool = True,
        wins: bool = True,
        cmap: float = "jet",
        ylabel: float = "frequencies",
        xlabel: float = "frames",
    ):
        plt.figure(figsize=figsize)
        plt.title("Spectrogram")
        ax = plt.subplot()
        im = ax.imshow(
            spec[0].detach().cpu().log(),
            aspect="auto",
            origin="lower",
            cmap=cmap,
            extent=[0, spec.shape[-1], 0, spec.shape[-2]],
        )
        plt.ylabel(ylabel)
        plt.xlabel(xlabel)
        if bar is True:
            plt.colorbar(im, ax=ax)
        plt.show()

        if weights is True:
            plt.figure(figsize=figsize)
            plt.title("Distribution of window lengths")
            ax = plt.subplot()
            im = ax.imshow(
                self.actual_win_length[: self.F].detach().cpu(),
                aspect="auto",
                origin="lower",
                cmap=cmap,
            )
            ax.set_ylabel(ylabel)
            ax.set_xlabel(xlabel)
            if bar is True:
                plt.colorbar(im, ax=ax)
                im.set_clim(self.win_min, self.win_max)
            plt.show()

        if self.tap_win is not None and wins is True:
            fig, ax = plt.subplots(figsize=figsize)
            ax.plot(self.T + 0.5 + x.squeeze().cpu().numpy(), linewidth=1)
            for i, start in enumerate(self.frames.detach().cpu()):
                ax.plot(
                    range(
                        int(start.floor().item()),
                        int(start.floor().item() + self.N),
                    ),
                    self.T
                    - i
                    - 1.3
                    + 150
                    * self.tap_win[:, i, :, :]
                    .mean(dim=1)
                    .squeeze()
                    .detach()
                    .cpu(),
                    c="#1f77b4",
                )

            if marklist is not None:
                for elem in marklist:
                    plt.axvline(elem, 0, self.T, c="gray")
            else:
                ax.axvline(x=0, ymin=0, ymax=self.T, c="gray")
                ax.axvline(x=x.shape[-1], ymin=0, ymax=self.T, c="gray")
            plt.show()







class FDSTFT(nn.Module):
    """Differentiable window length only short-time Fourier transform (DSTFT) module.
    only one window length fot stft optimizable by gradeint descent, no 

    Args:
    ----
        nn (_type_): _description_

    """

    def __init__(
        self: FDSTFT,
        x: torch.tensor,
        win_length: float,
        support: int,
        stride: int,
        win_requires_grad=True,
        win_min: float | None = None,
        win_max: float | None = None,
        tapering_function: str = "hann",
        sr: int = 16_000,
        window_transform=None,
        stride_transform=None,
    ):
        super().__init__()

        # Constants and hyperparameters
        self.N = support  # support size
        self.F = int(1 + self.N / 2)  # nb of frequencies
        self.B = x.shape[0]  # batch size
        self.L = x.shape[-1]  # signal length
        self.device = x.device
        self.dtype = x.dtype

        self.win_requires_grad = win_requires_grad
        self.tapering_function = tapering_function
        self.sr = sr
        self.tap_win = None

        # Register eps and min as a buffer tensor
        self.register_buffer(
            "eps",
            torch.tensor(
                torch.finfo(torch.float).eps,
                dtype=self.dtype,
                device=self.device,
            ),
        )
        self.register_buffer(
            "min",
            torch.tensor(
                torch.finfo(torch.float).min,
                dtype=self.dtype,
                device=self.device,
            ),
        )

        if win_min is None:
            self.win_min = self.N / 20
        else:
            self.win_min = win_min
        if win_max is None:
            self.win_max = self.N
        else:
            self.win_max = win_max

        # HOP LENGTH / FRAME INDEX
        # hop length constraints
        if stride_transform is None:
            self.stride_transform = self.__stride_transform
        else:
            self.stride_transform = stride_transform


        # WIN LENGTH
        # win length constraints
        if window_transform is None:
            self.window_transform = self.__window_transform
        else:
            self.window_transform = window_transform

        # Create the window length parameter and assign it the appropriate shape
        self.win_length = nn.Parameter(
            torch.full(
                (1,),
                abs(win_length),
                dtype=self.dtype,
                device=self.device,
            ),
            requires_grad=self.win_requires_grad,
        )


    def __window_transform(self: DSTFT, w_in):
        w_out = torch.minimum(
            torch.maximum(
                w_in,
                torch.full_like(
                    w_in, self.win_min, dtype=self.dtype, device=self.device,
                ),
            ),
            torch.full_like(
                w_in, self.win_max, dtype=self.dtype, device=self.device,
            ),
        )
        return w_out

    def __stride_transform(
        self: DSTFT, s_in: torch.Tensor,
    ):  # born stride entre 0 et 2N
        s_out = torch.minimum(
            torch.maximum(
                s_in,
                torch.full_like(
                    s_in, self.stride_min, dtype=self.dtype, device=self.device,
                ),
            ),
            torch.full_like(
                s_in, self.stride_max, dtype=self.dtype, device=self.device,
            ),
        )
        return s_out

    def __pow_transform(
        self: DSTFT, p_in: torch.Tensor,
    ):  # born stride entre 0 et 2N
        p_out = torch.minimum(
            torch.maximum(
                p_in,
                torch.full_like(
                    p_in, self.pow_min, dtype=self.dtype, device=self.device,
                ),
            ),
            torch.full_like(
                p_in, self.pow_max, dtype=self.dtype, device=self.device,
            ),
        )
        return p_out

    @property
    def actual_win_length(self: DSTFT):  # contraints
        return self.window_transform(self.win_length)

    @property
    def actual_strides(
        self,
    ):  # stride contraints, actual stride between frames
        return self.stride_transform(self.strides)

    @property
    def actual_pow(self: DSTFT):  # pow contraints
        return self.pow_transform(self.win_pow)

    @property
    def frames(self: DSTFT):
        # Compute the temporal position (indices) of frames (support)
        expanded_stride = self.actual_strides.expand((self.T,))
        frames = torch.zeros_like(expanded_stride)
        # frames[0] = - self.N / 2
        # if self.first_frame:
        #    frames[0] = (
        #        self.actual_win_length.expand((self.N, self.T))[:, 0].max(
        #            dim=0, keepdim=False,
        #        )[0]
        #        - self.N
        #    ) / 2
        frames -= self.N / 2 + self.init_stride
        frames += expanded_stride.cumsum(dim=0)

        return frames

    @property
    def effective_strides(self: DSTFT):
        # Compute the strides between window (and not frames)
        expanded_stride = self.actual_strides.expand((self.T,))
        effective_strides = torch.zeros_like(expanded_stride)
        effective_strides[1:] = expanded_stride[1:]
        cat = (
            torch.cat(
                (
                    torch.tensor(
                        [self.N], dtype=self.dtype, device=self.device,
                    ),
                    self.actual_win_length.expand((self.N, self.T)).max(
                        dim=0, keepdim=False,
                    )[0],
                ),
                dim=0,
            ).diff()
            / 2
        )
        effective_strides = effective_strides - cat
        return effective_strides

    def forward(self: DSTFT, x: torch.tensor) -> tuple:
        # Perform the forward STFT and extract the magnitude, phase, real, and imaginary parts
        stft = self.stft(x, "forward")
        spec = stft.abs().pow(self.pow)[:, : self.F] + torch.finfo(x.dtype).eps
        return spec, stft  # , real, imag, phase

    def backward(
        self: DSTFT, x: torch.tensor, dl_ds: torch.tensor,
    ) -> torch.tensor:
        # Compute the gradient of the loss w.r.t. window length parameter with the chain rule
        dstft_dp = self.stft(x, "backward")
        dl_dp = torch.conj(dl_ds) * dstft_dp
        dl_dp = dl_dp.sum().real.expand(self.win_length.shape)
        return dl_dp

    def stft(self: DSTFT, x: torch.tensor, direction: str):
        # batch_size, length, device, dtype = x.shape[0], x.shape[-1], x.device, x.dtype

        # Generate strided signal and shift idx_frac
        folded_x, idx_frac = self.unfold(x)  # B, T, N; T

        # Generate the tapering window function for the STFT
        self.tap_win = self.window_function(
            direction=direction, idx_frac=idx_frac,
        ).permute(1, 0)  # T, N

        # Compute tapered x
        self.folded_x = folded_x[:, :, :]  # B, T, N
        self.tap_win = self.tap_win[None, :, :]  # 1, T, 1
        self.tapered_x = self.folded_x * self.tap_win  # B, T, N,

        spectr = torch.fft.rfft(self.tapered_x)

        shift = torch.arange(
            end=self.F,
            device=self.device,
            dtype=self.dtype,
            requires_grad=False,
        )
        shift = idx_frac[:, None] * shift[None, :]  # T, N
        shift = torch.exp(2j * pi * shift / self.N)[None, ...]  # 1, T, N

        stft = spectr * shift
        return stft.permute(0, 2, 1)

    def inverse_dstft(self: DSTFT, stft: torch.Tensor) -> torch.tensor:
        """Compute inverse differentiable short-time Fourier transform (IDSTFT).

        Args:
        ----
            self (DSTFT): _description_
            stft (torch.Tensor): _description_

        Returns:
        -------
            torch.tensor: _description_

        """
        # shift
        # shift = torch.arange(
        #     end=self.F,
        #     device=self.device,
        #     dtype=self.dtype,
        #     requires_grad=False,
        # )
        # shift = idx_frac[:, None] * shift[None, :]  # T, N
        # stft = stft * torch.exp(2j * pi * shift / self.N)[None, ...]  # 1, T, N
        # print(stft.shape)

        # inverse
        # print(stft.shape, stft.dtype)
        ifft = torch.fft.irfft(stft, n=self.N, dim=-2)
        # print(ifft.shape, self.tap_win.sum(-1, keepdim=True).shape)
        ifft = (
            ifft  # * self.tap_win.sum(dim=-1, keepdim=True).permute(0, 2, 1)
        )
        # print(ifft.shape, ifft.dtype)wins2 = torch.zeros(x.shape[-1])


        # tapered
        # tap_win = torch.conj(self.tap_win) #= self.tap_win[None, :, :]  # 1, T, 1
        ifft = ifft.permute(0, -1, -2)  # * tap_win
        # print(ifft.shape, ifft.dtype)

        # print(ifft.sum())

        # fold
        x_hat = self.fold(ifft)

        return x_hat

    def unfold(self: DSTFT, x: torch.tensor) -> torch.tensor:
        # frames index and strided x
        idx_floor = self.frames.floor()
        # print(self.frames.shape, self.frames)
        idx_frac = self.frames - idx_floor
        idx_floor = idx_floor.long()[:, None].expand((
            self.T,
            self.N,
        )) + torch.arange(0, self.N, device=self.device)
        idx_floor[idx_floor >= self.L] = -1
        # print(self.B, idx_floor.shape, x.shape)
        folded_x = x[:, idx_floor]
        folded_x[:, idx_floor < 0] = 0
        return folded_x, idx_frac

    def fold(self: DSTFT, folded_x: torch.tensor) -> torch.tensor:
        x_hat = torch.zeros(
            self.B, self.L, device=self.device, dtype=self.dtype,
        )
        # print(x_hat.shape, self.B, self.L)
        #print(folded_x.shape)
        for t in range(self.T):
            start_idx = max(0, int(self.frames[t]))
            end_idx = min(self.L - 1, int(self.frames[t]) + self.N)
            start_dec = start_idx - int(self.frames[t])
            end_dec = end_idx - int(self.frames[t])
            x_hat[:, start_idx:end_idx] += folded_x[:, t, start_dec:end_dec]
        return x_hat

    def window_function(self: DSTFT, direction: str, idx_frac) -> torch.tensor:
        if self.tapering_function not in {"hann", "hanning"}:
            raise ValueError(
                f"tapering_function must be one of '{('hann', 'hanning')}', but got padding_mode='{self.tapering_function}'",
            )
        else:
            # Create an array of indices to use as the base for the window function
            base = torch.arange(
                0, self.N, 1, dtype=self.dtype, device=self.device,
            )[:, None].expand([-1, self.T])
            base = base - idx_frac
            # Expand the win_length parameter to match the shape of the base array

        # calculate the tapering function and its derivate w.r.t. window length
        mask1 = base.ge(torch.ceil((self.N - 1 + self.actual_win_length) / 2))
        mask2 = base.le(torch.floor((self.N - 1 - self.actual_win_length) / 2))
        if (
            self.tapering_function == "hann"
            or self.tapering_function == "hanning"
        ):
            if direction == "forward":
                self.tap_win = 0.5 - 0.5 * torch.cos(
                    2
                    * pi
                    * (base + (self.actual_win_length - self.N + 1) / 2)
                    / self.actual_win_length,
                )
                self.tap_win[mask1] = 0
                self.tap_win[mask2] = 0
                # self.tap_win = self.tap_win / self.tap_win.sum(
                #    dim=0, keepdim=True,
                # )
                return self.tap_win.pow(self.win_pow)

            elif direction == "backward":
                f = torch.sin(
                    2
                    * pi
                    * (base + (self.actual_win_length - self.N + 1) / 2)
                    / self.actual_win_length,
                )
                d_tap_win = (
                    -pi
                    / self.actual_win_length.pow(2)
                    * ((self.N - 1) / 2 - base)
                    * f
                )
                d_tap_win[mask1] = 0
                d_tap_win[mask2] = 0
                d_tap_win = d_tap_win / self.N * 2
                return d_tap_win
        return None

    def synt_win(self: DSTFT, direction: str, idx_frac) -> torch.tensor:
        return

    def coverage(self: DSTFT):  # in [0, 1]
        # compute coverage
        expanded_win, _ = self.actual_win_length.expand((self.N, self.T)).min(
            dim=0, keepdim=False,
        )
        cov = expanded_win[0]
        maxi = self.frames[0] + self.N / 2 + expanded_win[0] / 2
        for i in range(1, self.T):
            start = torch.min(
                self.L * torch.ones_like(expanded_win[i]),
                torch.max(
                    torch.zeros_like(expanded_win[i]),
                    self.frames[i] + self.N / 2 - expanded_win[i] / 2,
                ),
            )
            end = torch.min(
                self.L * torch.ones_like(expanded_win[i]),
                torch.max(
                    torch.zeros_like(expanded_win[i]),
                    self.frames[i] + self.N / 2 + expanded_win[i] / 2,
                ),
            )
            if end > maxi:
                cov += end - torch.max(start, maxi)
                maxi = end
        cov /= self.L
        return cov

    def plot(
        self: DSTFT,
        spec: torch.Tensor,
        x: torch.Tensor | None = None,
        marklist: Optional[List[Any]] = None,
        figsize=(6.4, 4.8),
        f_hat=None,
        fs=None,
        *,
        weights: bool = True,
        wins: bool = True,
        bar: bool = False,
        cmap: float = "jet",
        ylabel: float = "frequencies",
        xlabel: float = "frames",
    ):
        f_max = spec.shape[-2] if fs is None else fs / 2
        plt.figure(figsize=figsize)
        plt.title("Spectrogram")
        ax = plt.subplot()
        im = ax.imshow(
            spec[0].detach().cpu().log(),
            aspect="auto",
            origin="lower",
            cmap=cmap,
            extent=[0, spec.shape[-1], 0, f_max],
        )
        plt.ylabel(ylabel)
        plt.xlabel(xlabel)
        if bar == True:
            plt.colorbar(im, ax=ax)
        if f_hat is not None:
            for f in f_hat:
                plt.plot(f, linewidth=0.5, c="k", alpha=0.7)
        plt.show()

        if weights == True:
            plt.figure(figsize=figsize)
            plt.title("Distribution of window lengths")
            ax = plt.subplot()
            im = ax.imshow(
                self.actual_win_length[: self.F].detach().cpu(),
                aspect="auto",
                origin="lower",
                cmap=cmap,
            )
            ax.set_ylabel(ylabel)
            ax.set_xlabel(xlabel)
            if bar == True:
                plt.colorbar(im, ax=ax)
                im.set_clim(self.win_min, self.win_max)
            plt.show()

        if self.tap_win is not None and wins == True:
            fig, ax = plt.subplots(figsize=figsize)
            ax.plot(self.T + 0.5 + x.squeeze().cpu().numpy(), linewidth=1)
            for i, start in enumerate(self.frames.detach().cpu()):
                ax.plot(
                    range(
                        int(start.floor().item()),
                        int(start.floor().item() + self.N),
                    ),
                    self.T
                    - i
                    - 1.3
                    + self.tap_win[:, i, :].squeeze().detach().cpu(),
                    c="#1f77b4",
                )

            if marklist is not None:
                for elem in marklist:
                    plt.axvline(elem, 0, self.T, c="gray")
            else:
                ax.axvline(x=0, ymin=0, ymax=self.T, c="gray")
                ax.axvline(x=x.shape[-1], ymin=0, ymax=self.T, c="gray")
            plt.show()
