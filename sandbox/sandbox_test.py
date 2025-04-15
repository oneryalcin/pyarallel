

import os
import time
from pyarallel.config_manager import ConfigManager
from pyarallel import parallel

manager = ConfigManager()
# manager.reset()

# Set up test defaults
manager.update_config({
    "execution": {
        "default_max_workers": 4,
        "default_executor_type": "thread",
        "default_batch_size": 10
    }
})

manager.update_config({
        "execution": {
            "default_max_workers": 8,
            "nested_setting": {
                "value": "test"
            }
        }
    })
    
@parallel()
def sample_function(x):
    return x * 2

# Verify nested settings are inherited
assert sample_function.default_max_workers == 8

# Update nested config and verify changes propagate
manager.update_config({
    "execution": {
        "default_max_workers": 12
    }
})

@parallel()
def another_function(x):
    return x * 3

assert another_function.max_workers == 12


# manager = ConfigManager()
# manager.reset()
# manager.update_config({
#     "execution": {
#         "max_workers": 8,
#         "timeout": 60.0
#     },
#     "rate_limiting": {
#         "rate": 1000,
#         "interval": "minute"
#     }
# })


# manager.update_config({
#         'execution': {
#             'max_workers': 12,
#             'new_setting': 'value'
#         }
#     })

# manager.get('execution.max_workers') == 12


manager = ConfigManager()
manager.reset()
manager.set_execution(max_workers=4, timeout=30.0)
manager.set_rate_limiting(rate=500, interval="second")
manager.set_error_handling(retry_count=3)
manager.set_monitoring(enabled=True)




# os.environ["PYARALLEL_MAX_WORKERS"] = "6"
# os.environ["PYARALLEL_TIMEOUT"] = "45.0"

# load_env_vars()

# # print all env vars
# for key, value in os.environ.items():
#     print(f"{key}: {value}")
# config = ConfigManager().get_config()
# print(config)

# manager = ConfigManager()
# config = manager.get_config()
# assert config.max_workers == 6



