from __future__ import annotations

import torch
import torch.nn as nn


class BoxBoxWarpNet(nn.Module):
    def __init__(self, feature_dim: int = 82, hidden_size: int = 128, residual_limit: float = 0.12):
        super().__init__()
        self.residual_limit = float(residual_limit)
        self.conv = nn.Sequential(
            nn.Conv1d(feature_dim, 128, kernel_size=5, padding=2),
            nn.GELU(),
            nn.Conv1d(128, 128, kernel_size=5, padding=2),
            nn.GELU(),
            nn.Dropout(0.1),
        )
        self.lstm = nn.LSTM(
            input_size=128,
            hidden_size=hidden_size,
            num_layers=2,
            batch_first=True,
            bidirectional=True,
            dropout=0.1,
        )
        self.head = nn.Sequential(
            nn.Linear(hidden_size * 2, hidden_size),
            nn.GELU(),
            nn.Linear(hidden_size, 1),
        )

    def _forward_raw_curve(self, x: torch.Tensor, mask: torch.Tensor | None = None) -> torch.Tensor:
        baseline = x[:, :, -1]
        x = x.transpose(1, 2)
        x = self.conv(x)
        x = x.transpose(1, 2)
        x, _ = self.lstm(x)
        residual = torch.tanh(self.head(x).squeeze(-1)) * self.residual_limit
        if mask is not None:
            residual = residual * mask
        curve = torch.clamp(baseline + residual, 0.0, 1.0)
        return curve

    def forward_export(self, x: torch.Tensor, mask: torch.Tensor | None = None) -> torch.Tensor:
        curve = self._forward_raw_curve(x, mask=mask)
        if mask is not None:
            denom = (curve * mask).amax(dim=1, keepdim=True).clamp_min(1e-6)
            y = curve / denom
            return y * mask
        denom = curve[:, -1:].clamp_min(1e-6)
        return curve / denom

    def forward(self, x: torch.Tensor, mask: torch.Tensor | None = None) -> torch.Tensor:
        # x: [B, T, F]
        curve = self._forward_raw_curve(x, mask=mask)
        curve = torch.cummax(curve, dim=1).values
        if mask is not None:
            denom = (curve * mask).amax(dim=1, keepdim=True).clamp_min(1e-6)
            y = curve / denom
            return y * mask
        denom = curve[:, -1:].clamp_min(1e-6)
        return curve / denom
