import pickle
from qudi.logic.pulsed.pulsed_data.sequence_generator_logic_data import SamplingInformation

# 1. Provide the path to one of your legacy .ensemble files
file_path = r"C:\Users\BM\saved_pulsed_assets\rabi.ensemble"

with open(file_path, 'rb') as f:
    ensemble = pickle.load(f)

print(f"Current type of sampling_information: {type(ensemble.sampling_information)}")

# 2. If it's a dict, manually run your migration logic to see if it works
if isinstance(ensemble.sampling_information, dict):
    new_info = SamplingInformation.from_dict(ensemble.sampling_information)
    print("Migration successful! New type:")
    print(type(new_info))
else:
    print("Already a dataclass.")