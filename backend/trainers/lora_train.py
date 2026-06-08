from pathlib import Path


def build_lora_command(base_model: str, train_file: str, output_dir: str):
    return [
        "python",
        "-m",
        "transformers.trainer",
        "--model_name_or_path",
        base_model,
        "--train_file",
        train_file,
        "--output_dir",
        output_dir,
    ]


def build_qlora_command(base_model: str, train_file: str, output_dir: str):
    return [
        "python",
        "-m",
        "transformers.trainer",
        "--model_name_or_path",
        base_model,
        "--train_file",
        train_file,
        "--output_dir",
        output_dir,
        "--quantization_bit",
        "4",
    ]


def write_train_runbook(base_model: str, train_file: str, output_dir: str, out_path: str):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# LoRA / QLoRA Runbook\n\n")
        f.write(f"Base model: {base_model}\n")
        f.write(f"Train file: {train_file}\n")
        f.write(f"Output dir: {output_dir}\n")
    return out_path
