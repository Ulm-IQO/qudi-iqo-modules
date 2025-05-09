# Changelog - LENS additions

This file lists the various changes made by the LENS fork to the original repository. In time, some of these might be upstreamed to the original repository. We try to rebase our fork on the original from time to time.

# New interfaces

* A new interface for scanning excitation spectroscopy (82e1388ee1e8f9bea6cd6fb4d597fb8da2b1f544)

# New hardwares

* Thorlabs flip mounts (8d26f61c0b3ab8b3df9871febe0e90a11aed4255)
* Integration of Sirah Matisse laser (aff2878e87b2635d6696bd1013142dbbf8ecbd9b)
* Integration of MOGLabs' cateye laser (e70faec9c2c9d9c57036e7f8e2dc665a859d4d0a)
* Scanning excitation spectroscopy interfuse to use NI cards (4704291fafed04bc2e284be96fe88d0a4c4c10b1)
* Dummy excitation scanner (a98e288d85b5b711c7ef085b39ef0ab9ca05fea2)

# New logics

* Power sweeping (or really anything that's logged through a time series) (2a1301ec0144dc1dd9b8b4d78d58935497b1350c)
* Scanning excitation logic to work with the scanning excitation interface hardwares (1e7affe8ff7e5ef48c76b5fea83ef8aa44f11964)

# New GUIs

* Scanning excitation spectroscopy gui, derived from the spectrometer gui (db61389b27fbb982a0f163a0a314ef5a09c6517f).

# Modifications to pre-existing elements

* Modified the scanning probe logic to save in the global metadata the current position, so we have it in most saved files (aa502c66f9b6d9aa0f776e8d75c1fb6a3c6a2ff3)
* Some fixes when saving with qdplot_logic (038f42703ffe240b1ec4f2445f440e9a4e5521f0)
* Modification of the time series GUI to allow prevent the autoscaling of the y axis (f4e930044c62230ebdc90af40075a2de672e9eb7)
