"""Load the DeepEval conversational golden dataset."""

from pathlib import Path

from deepeval.dataset import ConversationalGolden, EvaluationDataset

DATASET_PATH = Path(__file__).resolve().parents[1] / "scenario.json"


def load_goldens() -> list[ConversationalGolden]:
    """Load conversational scenarios from the repository JSON file."""

    dataset = EvaluationDataset()
    dataset.add_goldens_from_json_file(file_path=str(DATASET_PATH))
    return dataset.goldens
