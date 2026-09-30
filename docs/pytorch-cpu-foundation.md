# PyTorch CPU Foundation (#652)

Optional, CPU-only PyTorch integration for Alice Pro. Yandex remains the primary AI provider.

## Opt-in

PyTorch is disabled by default. Normal startup never imports torch.

**Environment variable (future wiring, not yet in app.py):**
```
ALICE_PYTORCH=1
```

**Programmatic:**
```python
from pytorch_adapter import PyTorchAdapter, register_pytorch_tools
from tool_registry import registry

adapter = PyTorchAdapter(enabled=True)
register_pytorch_tools(registry, adapter=adapter)
```

## Installation

CPU-only profile (no GPU, no CUDA):

```bash
pip install -r requirements-pytorch-cpu.txt
```

Verify:
```bash
python -c "import torch; assert torch.version.cuda is None; print(torch.__version__)"
```

**Supported platforms:** Linux x86-64 with Python 3.14.  
**Not supported:** Android, desktop packaging, default server installs, GPU/CUDA variants.

## Adapter states

| State | Cause |
|---|---|
| `disabled` | Default; `enabled=False` or torch not installed. No import attempted. |
| `missing` | `enabled=True`, `import torch` raised `ModuleNotFoundError`. |
| `unavailable` | `enabled=True`, a native/transitive dependency failed to load. |
| `ready` | torch imported successfully, CPU smoke works. |

## Smoke operation

A fixed, deterministic, bounded CPU float32 computation:

```
tensor([1, 2, 3]) × 2 = [2.0, 4.0, 6.0]
```

No model download, no training, no GPU, no global setting mutations.

## Disable / rollback

1. Remove `requirements-pytorch-cpu.txt` from the install command.
2. Uninstall: `pip uninstall torch -y`
3. Do not set `ALICE_PYTORCH=1`.

The adapter is fully lazy — uninstalling torch or not setting the opt-in flag is sufficient. No configuration rollback required.

## Limits

This foundation provides only:
- State reporting (`pytorch_status` tool)
- A fixed bounded smoke test (`pytorch_smoke` tool)

It does not provide model inference, training, custom checkpoints, GPU access, torchvision, torchaudio, or any ML pipeline beyond the fixed smoke.
