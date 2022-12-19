# Release Workflow

This document describes how IQO manages the release of a new `qudi-iqo-modules` version to github.
If you are not the responsible release manager, it should be of no concern for you.


## Once: Becoming the release manager

1. Create seperate accounts at https://pypi.org/ and https://test.pypi.org/
2. For each account, log in and navigate to Account Settings/ Add API Token
3. Create an API token and leave this browser tab open
4. Login to github and navigate to Ulm-IQO/qudi-iqo-modules/Settings/Secrets/Repository secrets
5. Create a new secret. It's name must fit the VARIABLES that are defined in the release scripts
   in `qudi-iqo-modules/.github/workflows/release_pypi.yml` and `./release_test_pypi.yml`. In our case that's
   `PYPI_API_TOKEN` and `TEST_PYPI_API_TOKEN`.
    
6. Copy the token you created on pypi.org from the pypi tab to the github secrets. It will start by `pypi-`.
7. Trigger an action and validate everything worked. Eg. you can create an commit to `qudi-iqo-modules/VERSIONS`.
   The success of the test release can be seen in Ulm-IQO/qudi-iqo-modules/Actions
   

## Before a release
1. The maintenance team decides when a certain commit version is well enough tested for a release.
   For the testing, at least one setup must run on a stable set of dependency versions for a while. These versions will be
   fixed `==` in the release `setup.py`.
   

## Performing a release

1. Increment version number in `qudi-iqo-modules/VERSIONS`. This will already trigger a release to test.pypi.
2. Check `qudi-iqo-modules/setup.py` and fix all versions with `==` according to the versions of the test setup
   mentioned above. Test whether the installation runs flawlessly with this setup.py and quickly test running qudi with dummy.
3. Update `qudi-iqo-modules/docs/changelog.md`. All collected differences in the pre-release section should go to
   a new subsection that's titled according to the new version number. Add a fresh, empty pre-release section.    

4. Execute the release by navigating in github to qudi-iqo-modules. On the right bar you can 'create a new release' in 
   the Releases section.
   By convention we tag releases by a string like 'Release v0.1.0'.
   As the description text, you can copy the respective release section from `qudi-iqo-modules/docs/changelog.md`.
   
5. Change the requirement equalities (`==`) in `qudi-iqo-modules/setup.py` back to `>=`.
6. Iterate the version number in `qudi-iqo-modules/VERSIONS` from release to development. Eg. 1.0.0 to 1.0.1.dev0
7. Edit `qudi-iqo-modules/.github/ISSUE_TEMPLATE/bug_report.yaml` and add the option to choose the release version
   that you just released.
8. Edit the Troubleshooting section in `qudi-iqo-modules/docs/installation_guide.md` to point at installing the latest
   release. Eg. `git checkout tags/v0.4.0`

9. Lean back and get some cold drink. You just released a new qudi-iqo-modules version! 