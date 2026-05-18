Two workflows test_req.yml and test_sv.yml that run routinely to ensure the tests don't break

# test_req.yml 

Set to run at 00:30 everyday, mainly checks if any version updates in dependencies breaks the tests. It has two jobs :
## check-package-updates
This job doesn't install dependencies but compares the frozen package versions saved to a different branch [workflow_save](https://github.com/Ulm-IQO/qudi-iqo-modules/tree/workflow_save) with the latest version available on the [Python package index](https://pypi.org/) . The frozen versions on workflow_save are updated after every succssful run of the workflow. If any new version is found on PyPI, a variable updates-available is set which triggers execution of the next job.
The dependencies that are pinned to a fixed version or have an upper bound in pyproject.toml are ignored. This is done by maintaining a saved copy of pyproject.toml in the worflow_save branch in the test job.
## test
This job is conditionally triggered after check-package-updates if any updates are available. If yes, qudi is installed and tests are run.
If the tests fail, the installed version of requirements are compared to the saved ones from the previous successful run (This is the same saved frozen version list used in the first job). A failure email is sent mentioning any differences in package versions. 
Upon a successful run, the frozen version list is updated. 
All the scripts used and version lists saved are in the workflow_save branch in workflow_utils folder. 

# test_sv.yml
Set to run once every Monday, it runs a single test that resets status variables and runs all the modules. It then compares the values of status variables.
Has only one job that installs qudi and runs the tests, upon success dumps the status variables to workflow_save branch, and upon failure compares the current values of status variables to the ones saved from the previous run.
All the scripts and status variable saves are in worflow_save branch.