# Build and privately install from Windows

No App Store submission and no Docker are involved. You will need the physical iPhone, a USB cable, a free Apple account (or paid developer account), and an Apple Xcode download containing the iOS SDK. Do not put passwords, signing certificates, or authentication tokens into this project or source ZIP.

## Current computer

Ubuntu WSL is available. The project has a private tools directory for Swift 6.4.0 and xtool 1.20.1. Standard compiler dependencies were installed in Ubuntu. **The Apple Darwin/iOS SDK has not been installed, and no signed or unsigned IPA has been built.**

The private `.tools` folder is excluded from the portable source ZIP. If using another computer, follow the [official xtool Windows/WSL installation guide](https://github.com/xtool-org/xtool/blob/main/Documentation/xtool.docc/Installation-Linux.md) first. The build script also accepts `swift` and `xtool` already installed on the WSL PATH.

## 1. Download Apple's SDK container

Open [Apple's developer downloads](https://developer.apple.com/download/all/?q=Xcode) in your browser, sign in, and download **Xcode 27**, matching the Swift 6.4 toolchain used here. This is a large `.xip` archive. Keep it in a folder such as Windows Downloads. You do not run Xcode on Windows; xtool extracts its SDK.

Apple's download and account prompts need your interaction. Do not send your password or two-factor code in chat. If you choose a different Xcode version, use the corresponding Swift version documented by xtool; don't mix compiler/SDK versions blindly.

## 2. Configure xtool

In PowerShell:

```powershell
cd 'E:\UBC\MANU 453\iphone'
.\Build-iPhone.ps1 -Action setup
```

Follow the interactive login prompt. xtool supports free accounts through its password login option; its API-key option requires paid membership. For the Xcode path enter the **WSL path**, for example:

```text
/mnt/c/Users/gabri/Downloads/Xcode_27.0.xip
```

Use the exact filename you downloaded. Once setup finishes:

```powershell
.\Build-iPhone.ps1 -Action check
```

It should report the Swift compiler, xtool, and Darwin SDK. This confirms tools are present, not that the iPhone app has compiled yet.

## 3. Connect the phone

Follow Microsoft's [USB device forwarding guide for WSL](https://learn.microsoft.com/windows/wsl/connect-usb). Install `usbipd-win` if needed; list devices with `usbipd list`, then bind/attach **the iPhone's actual bus ID** to WSL. Binding requires an administrator shell. Do not paste a guessed bus ID.

Inside Ubuntu install `usbmuxd` and `libimobiledevice-utils` if missing. Unlock the iPhone and approve **Trust this computer** when prompted. Enable **Settings → Privacy & Security → Developer Mode** if iOS requests it; that may restart the phone.

```powershell
.\Build-iPhone.ps1 -Action devices
```

If USB forwarding is troublesome, xtool's installation guide links a Windows Apple-device connection relay alternative. Only one phone should be connected for the initial deployment to avoid selecting the wrong device.

## 4. Build and install the app

```powershell
.\Build-iPhone.ps1 -Action install
```

This runs a release build and asks xtool to sign and install it for the connected phone. The first build may take several minutes. If iOS says **Untrusted Developer**, trust the signing identity under **Settings → General → VPN & Device Management**, then launch Rockform Capture manually.

The bundle ID in `xtool.yml` is `com.rockform.capture.prototype`. If Apple reports it unavailable, change it to a unique identifier using your own project/team name. No App Store listing is needed.

## 5. Package for your teammate

The easiest initial test is to connect your teammate's phone to this PC and repeat `install` with that phone selected. Alternatively, give your teammate the source ZIP and let them build/sign with their own setup.

To create a transferable build artifact:

```powershell
# Unsigned: build artifact only; cannot launch on an iPhone as-is.
.\Build-iPhone.ps1 -Action package

# Signed: requires your configured signing account and eligible devices.
.\Build-iPhone.ps1 -Action signed-package
```

xtool prints the resulting IPA path, generally inside its `xtool` output directory. A signed IPA only works on devices allowed by its provisioning profile. Register/provision your teammate's device or have them re-sign the app; sending the file by itself is insufficient. Do not assume a package signed only for your phone will run on theirs.

Apple's free Personal Team profiles expire after seven days; rebuild/reinstall when needed. See [Apple's account limits](https://developer.apple.com/help/account/basics/about-your-developer-account). Development signing is separate from App Store distribution.

## Verification before field use

On your phone: Demo preview/export, Photos mode, permission denial/retry, saved ZIP sharing, app backgrounding. On the LiDAR phone: support detection, tracking, anchored crop region, thin/concave surfaces, missing areas, interruption, save/reopen/share, scale checks. Import at least one actual phone OBJ into Rockform. These device checks are pending until the SDK and phones are available.
