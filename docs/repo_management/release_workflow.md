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
   
2. Make sure that packages that are fixed (`==`) in the latest qudi-core release, are left mentioned, but 
unspecified in qudi-iqo-modules `setup.py`. This is most likely the case for the `PySide2` requirement.
   
3. Go through the list of merged PRs since the last release. Check that the changes have been documented in
   `qudi-iqo-modules/docs/changelog.md`. From experience, not all PRs edit the changelog as required. 
   

## Performing a release

1. Create a release branch eg, `release_vX.0.0` and collect all needed changes in here.

2. Check `qudi-iqo-modules/setup.py` and fix all versions with `==` according to the versions of the test setup
   mentioned above. 
   
3. Test whether the installation runs flawlessly. 
   To this end, create a new Python environment and activate it.
   Install qudi-core via
         
         python -m install qudi-core 
   
   and install qudi-iqo-modules via

         git clone https://github.com/Ulm-IQO/qudi-iqo-modules.git --branch release_vX.0.0
         cd qudi-iqo-modules
         python -m pip install -e .

   Quickly test running qudi with dummy.

4. Update `qudi-iqo-modules/docs/changelog.md`. All collected differences in the pre-release section should go to
   a new subsection that's titled according to the new version number. Add a fresh, empty pre-release section.    

5. Edit `qudi-iqo-modules/.github/ISSUE_TEMPLATE/bug_report.yaml` and add the option to choose the release version
   that you just released.
6. Edit the Troubleshooting section in `qudi-iqo-modules/docs/installation_guide.md` to point at installing the latest
   release. Eg. `git checkout tags/v0.4.0`

7. Only after testing, create a PR and merge `release_vX.0.0` into `main`.

8. Increment version number in `qudi-iqo-modules/VERSIONS`.
   This will already trigger a release from the main branch to test.pypi.
   
9. Execute the release by navigating in github to qudi-iqo-modules. On the right bar you can 'create a new release' in 
   the Releases section.
   By convention we tag releases by a string like 'Release v0.1.0'.
   As the description text, you can copy the respective release section from `qudi-iqo-modules/docs/changelog.md`.
   Don't add a heading like "Relaase v0.1.0", this is automatically created by github.   

10. Change the requirement equalities (`==`) (branch `main`) in`qudi-iqo-modules/setup.py` back to `>=`.
11. Iterate the version number in `qudi-iqo-modules/VERSIONS` from release to development. Eg. 1.0.0 to 1.0.1.dev0

12. Lean back and get some cold drink. You just released a new qudi-iqo-modules version! 