import SwiftUI
import SceneKit
import ARKit
import CaptureCore

private let mint=Color(red:0.38,green:0.87,blue:0.74)

@MainActor
struct CaptureScreen: View {
    @StateObject private var capture=CaptureController()
    @Environment(\.scenePhase) private var scenePhase
    @State private var showLibrary=false
    @State private var showNewConfirmation=false
    @State private var pendingMode: CaptureMode?
    @State private var showHelp=false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment:.leading,spacing:18) {
                    HStack {
                        Label(capture.supportsLiDAR ? "LiDAR available":"No LiDAR on this phone",systemImage:capture.supportsLiDAR ? "sensor.tag.radiowaves.forward.fill":"iphone")
                            .font(.caption).foregroundStyle(mint)
                        Spacer()
                        Text("LOCAL CAPTURE").font(.system(size:10,weight:.bold,design:.monospaced)).foregroundStyle(.secondary)
                    }
                    HStack(spacing:8) {
                        ForEach(CaptureMode.allCases) { mode in
                            Button {
                                if mode != capture.mode {
                                    if capture.hasCapture { pendingMode=mode; showNewConfirmation=true }
                                    else { capture.selectMode(mode) }
                                }
                            } label: {
                                Text(mode.rawValue).font(.subheadline.weight(.semibold)).frame(maxWidth:.infinity).padding(.vertical,11)
                                    .background(capture.mode == mode ? mint:Color.white.opacity(0.06),in:RoundedRectangle(cornerRadius:12))
                                    .foregroundStyle(capture.mode == mode ? Color.black:Color.white)
                            }.disabled(capture.isCapturing || capture.isExporting || (mode == .lidar && !capture.supportsLiDAR))
                        }
                    }
                    viewer
                    Text(capture.status).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
                    if capture.mode == .lidar && !capture.isReviewing && !capture.isCapturing {
                        VStack(alignment:.leading,spacing:6) {
                            HStack { Text("Patch width"); Spacer(); Text("\(Int(capture.patchWidth*100)) cm").foregroundStyle(mint) }
                            Slider(value:$capture.patchWidth,in:0.3...1.2,step:0.1).tint(mint)
                            Text("Square capture area, 40 cm deep. Its position is fixed when you start.").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    controls
                    if capture.isReviewing { exportDetails }
                    Text("Shape capture prototype · Verify dimensions before use.").font(.caption2).foregroundStyle(.secondary).padding(.top,4)
                }.padding(20)
            }
            .background(Color(red:0.055,green:0.075,blue:0.09))
            .navigationTitle("Rockform Capture").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement:.topBarLeading) { Button { showHelp=true } label: { Image(systemName:"questionmark.circle") }.accessibilityLabel("Capture help") }
                ToolbarItem(placement:.topBarTrailing) { Button { capture.refreshLibrary(); showLibrary=true } label: { Image(systemName:"folder") }.accessibilityLabel("Saved captures") }
            }
            .onAppear { capture.prepareCamera() }
            .onChange(of:scenePhase) { _, phase in capture.setActive(phase == .active) }
            .alert("Capture message",isPresented:Binding(get:{ capture.errorMessage != nil },set:{ if !$0 { capture.errorMessage=nil } })) {
                Button("OK") { capture.errorMessage=nil }
            } message: { Text(capture.errorMessage ?? "") }
            .confirmationDialog("Discard this working capture? Saved ZIP files are kept.",isPresented:$showNewConfirmation,titleVisibility:.visible) {
                Button("Discard and continue",role:.destructive) {
                    if let mode=pendingMode { capture.selectMode(mode) } else { capture.newCapture() }
                    pendingMode=nil
                }
                Button("Cancel",role:.cancel) { pendingMode=nil }
            }
            .sheet(isPresented:Binding(get:{ capture.shareURL != nil },set:{ if !$0 { capture.shareURL=nil } })) {
                if let url=capture.shareURL { ShareSheet(url:url) }
            }
            .sheet(isPresented:$showLibrary) { library }
            .sheet(isPresented:$showHelp) { help }
        }.tint(mint)
    }

    private var viewer: some View {
        ZStack(alignment:.topLeading) {
            if let mesh=capture.reviewMesh {
                MeshPreview(mesh:mesh).id(capture.reviewID).frame(height:310)
            } else {
                CameraPreview(view:capture.cameraView).frame(height:310)
                if !capture.isReviewing {
                    Image(systemName:"plus").font(.title2.weight(.light)).foregroundStyle(mint)
                        .frame(maxWidth:.infinity,maxHeight:.infinity).allowsHitTesting(false)
                }
            }
            VStack(alignment:.leading,spacing:6) {
                Text(capture.mode == .demo ? "SYNTHETIC DEMO" : capture.isReviewing ? "REVIEW" : capture.isCapturing ? "RECORDING" : "AIM AT THE ROCK")
                    .font(.system(size:10,weight:.bold,design:.monospaced))
                    .padding(7).background(.black.opacity(0.7),in:Capsule())
                if capture.mode != .demo && !capture.isReviewing {
                    Text(capture.tracking).font(.caption)
                    if let range=capture.rangeMeters { Text(String(format:"Center %.2f m",range)).font(.caption) }
                    if capture.mode == .lidar { Text(capture.confidenceText).font(.caption2) }
                }
                Spacer()
                HStack {
                    Label("\(capture.triangleCount.formatted()) faces",systemImage:"triangle")
                    Spacer()
                    Label("\(capture.photoCount)",systemImage:"photo")
                }.font(.caption).padding(8).background(.black.opacity(0.65),in:RoundedRectangle(cornerRadius:8))
            }.padding(12).allowsHitTesting(false)
        }.frame(height:310).background(.black).clipShape(RoundedRectangle(cornerRadius:18))
    }

    @ViewBuilder private var controls: some View {
        if capture.isExporting {
            HStack { ProgressView(); Text("Writing your capture…"); Spacer(); Button("Cancel",role:.cancel) { capture.cancelExport() } }
        } else if !capture.isReviewing && capture.mode != .demo {
            HStack {
                if capture.mode == .lidar {
                    Button(capture.isCapturing ? "Finish scan":"Start scan") {
                        if capture.isCapturing { capture.finishCapture() } else { capture.startScan() }
                    }.buttonStyle(.borderedProminent).disabled(!capture.cameraReady)
                } else {
                    Button("Finish photos") { capture.finishCapture() }.buttonStyle(.borderedProminent).disabled(capture.photoCount == 0)
                }
                Button { capture.takePhoto() } label: { Label("Photo",systemImage:"camera") }
                    .buttonStyle(.bordered).disabled(!capture.cameraReady || capture.photoCount>=20)
            }
            if capture.mode == .lidar { Toggle("Show captured mesh",isOn:$capture.showWireframe).font(.subheadline).tint(mint) }
            if !capture.cameraReady { Button("Start camera") { capture.prepareCamera() }.buttonStyle(.bordered) }
        }
        if capture.isReviewing && !capture.isExporting {
            HStack {
                Button("Save & share ZIP") { capture.saveCapture() }.buttonStyle(.borderedProminent).disabled(!capture.canExport)
                Button("New") { pendingMode=nil; showNewConfirmation=true }.buttonStyle(.bordered)
            }
        }
    }

    private var exportDetails: some View {
        VStack(alignment:.leading,spacing:12) {
            Text("Capture notes").font(.headline)
            if let mesh=capture.reviewMesh {
                let b=mesh.dimensions
                Text(String(format:"Bounds %.1f × %.1f × %.1f mm",b.x,b.y,b.z)).font(.caption).foregroundStyle(mint)
            }
            TextField("Measured reference (mm, optional)",text:$capture.referenceMM).keyboardType(.decimalPad)
            TextField("Describe the two measured points",text:$capture.referenceDescription)
            TextField("Rock / location notes (optional)",text:$capture.notes,axis:.vertical).lineLimit(2...4)
            Text("The reference is recorded for checking in Rockform. It does not rescale the scan automatically.").font(.caption).foregroundStyle(.secondary)
        }.textFieldStyle(.roundedBorder).disabled(capture.isExporting)
    }

    private var library: some View {
        NavigationStack {
            List {
                if capture.savedFiles.isEmpty { Text("Saved captures will appear here.").foregroundStyle(.secondary) }
                ForEach(capture.savedFiles,id:\.self) { url in
                    ShareLink(item:url) { Label(url.lastPathComponent,systemImage:"square.and.arrow.up").font(.caption) }
                }
            }.navigationTitle("Saved captures").toolbar { ToolbarItem(placement:.confirmationAction) { Button("Done") { showLibrary=false } } }
        }
    }
    private var help: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment:.leading,spacing:18) {
                    Text("One patch at a time").font(.title2.bold())
                    Text("Use even light and a dry, textured surface. Aim at the center of a broad feature, wait for tracking, then start. Move slowly sideways and capture its top, bottom, and sides. The capture box stays fixed to the rock.")
                    Text("Inspect before exporting").font(.headline)
                    Text("Finish to rotate the captured mesh with one finger and zoom with two. Missing areas need another scan. This version does not fill holes or reconstruct unseen surfaces.")
                    Text("Testing without LiDAR").font(.headline)
                    Text("Demo mode creates a synthetic 300 mm square surface to test ZIP export and Rockform import. Photos mode saves reference images only; it does not create a 3D model.")
                    Text("Transfer to Windows").font(.headline)
                    Text("Save & share creates a ZIP on your phone. Transfer it using Files, cloud storage, or Apple Devices file sharing. Unzip on Windows, import surface-mm.obj into Rockform, and select Millimeters. Keep capture.json with the original scan.")
                    Text("Privacy and limits").font(.headline)
                    Text("No login, server, analytics, or automatic upload. Captures stay local until you share. This lightweight app saves ARKit meshes and up to 20 reference photos; it does not record raw depth frames or run photogrammetry. Use it for shape prototypes and validate dimensions independently.")
                }.padding(24)
            }.navigationTitle("Capture guide").toolbar { ToolbarItem(placement:.confirmationAction) { Button("Done") { showHelp=false } } }
        }
    }
}

private struct CameraPreview: UIViewRepresentable {
    let view: ARSCNView
    func makeUIView(context:Context) -> ARSCNView { view }
    func updateUIView(_ uiView:ARSCNView,context:Context) {}
}
private struct MeshPreview: UIViewRepresentable {
    let mesh: SurfaceMesh
    func makeUIView(context:Context) -> SCNView {
        let view=SCNView(); view.backgroundColor=UIColor(red:0.055,green:0.075,blue:0.09,alpha:1)
        view.allowsCameraControl=true; view.autoenablesDefaultLighting=true; view.antialiasingMode = .multisampling4X
        let scene=SCNScene()
        let node=SCNNode(geometry:CaptureController.sceneGeometry(mesh,wireframe:false)); scene.rootNode.addChildNode(node)
        let (lo,hi)=node.boundingBox
        let center=SCNVector3((lo.x+hi.x)/2,(lo.y+hi.y)/2,(lo.z+hi.z)/2)
        let camera=SCNNode(); camera.camera=SCNCamera(); camera.camera?.zNear=0.001; camera.camera?.zFar=20
        let distance=max(0.3,max(hi.x-lo.x,hi.y-lo.y)*1.7)
        camera.position=SCNVector3(center.x,center.y,center.z+distance); scene.rootNode.addChildNode(camera)
        view.scene=scene; view.pointOfView=camera
        view.defaultCameraController.target=center
        return view
    }
    func updateUIView(_ uiView:SCNView,context:Context) {}
}
private struct ShareSheet: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context:Context) -> UIActivityViewController { UIActivityViewController(activityItems:[url],applicationActivities:nil) }
    func updateUIViewController(_ uiViewController:UIActivityViewController,context:Context) {}
}
