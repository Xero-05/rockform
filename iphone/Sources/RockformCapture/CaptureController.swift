import Foundation
import SwiftUI
import ARKit
import SceneKit
import AVFoundation
import CoreImage
import ImageIO
import CaptureCore

enum CaptureMode: String, CaseIterable, Identifiable {
    case lidar = "LiDAR", photos = "Photos", demo = "Demo"
    var id: String { rawValue }
}

struct ReferencePhoto: Sendable {
    let jpeg: Data
    let timestamp: Double
    let cameraTransform: [Float]
    let intrinsics: [Float]
}

final class ExportCancellation: @unchecked Sendable {
    private let lock=NSLock()
    private var value=false
    func cancel() { lock.lock(); value=true; lock.unlock() }
    var isCancelled: Bool { lock.lock(); defer { lock.unlock() }; return value }
}

/// All AR session callbacks and observable UI state run on the main queue.
/// Background export receives only independent value snapshots, never AR buffers.
@MainActor
final class CaptureController: NSObject, ObservableObject, @preconcurrency ARSessionDelegate {
    let supportsLiDAR = ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)
    let cameraView=ARSCNView(frame: .zero)
    @Published private(set) var mode: CaptureMode = .demo
    @Published private(set) var status="Choose a capture mode."
    @Published private(set) var tracking="Camera off"
    @Published private(set) var cameraReady=false
    @Published private(set) var isCapturing=false
    @Published private(set) var isReviewing=false
    @Published private(set) var isExporting=false
    @Published private(set) var triangleCount=0
    @Published private(set) var photoCount=0
    @Published private(set) var reviewMesh: SurfaceMesh?
    @Published private(set) var reviewID=UUID()
    @Published private(set) var rangeMeters: Double?
    @Published private(set) var confidenceText="Depth unavailable"
    @Published var patchWidth=0.6
    @Published var referenceMM=""
    @Published var referenceDescription=""
    @Published var notes=""
    @Published var showWireframe=true { didSet { overlayRoot.isHidden = !showWireframe } }
    @Published var errorMessage: String?
    @Published var shareURL: URL?
    @Published private(set) var savedFiles: [URL]=[]
    let captureDepthMM=400.0
    private var frame: CaptureFrame?
    private var frameTransform=matrix_identity_float4x4
    private var meshes: [UUID:SurfaceMesh]=[:]
    private var meshNodes: [UUID:SCNNode]=[:]
    private let overlayRoot=SCNNode()
    private var lastUpdates: [UUID:TimeInterval]=[:]
    private var lastFrameUpdate=0.0
    private var referencePhotos: [ReferencePhoto]=[]
    private let imageContext=CIContext()
    private var exportCancellation: ExportCancellation?
    private var cameraRequestPending=false
    private var sceneIsActive=true

    override init() {
        super.init()
        cameraView.scene=SCNScene()
        cameraView.scene.rootNode.addChildNode(overlayRoot)
        cameraView.session.delegate=self
        cameraView.session.delegateQueue = .main
        cameraView.automaticallyUpdatesLighting=false
        cameraView.preferredFramesPerSecond=30
        mode = supportsLiDAR ? .lidar : .demo
        if mode == .demo { loadDemo() }
        refreshLibrary()
    }

    var canExport: Bool { isReviewing && !isExporting && (reviewMesh != nil || photoCount>0) }
    var hasCapture: Bool { isCapturing || reviewMesh != nil || photoCount>0 }

    func selectMode(_ selected: CaptureMode) {
        guard !isExporting, !isCapturing else { return }
        guard selected != .lidar || supportsLiDAR else { return }
        clearCapture()
        mode=selected
        if selected == .demo { loadDemo() } else { prepareCamera() }
    }

    func newCapture() {
        guard !isExporting else { return }
        clearCapture()
        if mode == .demo { loadDemo() } else { prepareCamera() }
    }

    private func clearCapture() {
        cameraView.session.pause(); cameraReady=false; isCapturing=false; isReviewing=false
        meshes.removeAll(); meshNodes.removeAll(); lastUpdates.removeAll()
        overlayRoot.childNodes.forEach { $0.removeFromParentNode() }
        frame=nil; reviewMesh=nil; reviewID=UUID(); referencePhotos.removeAll(); photoCount=0; triangleCount=0
        rangeMeters=nil; referenceMM=""; referenceDescription=""; notes=""; errorMessage=nil
        tracking="Camera off"; confidenceText="Depth unavailable"
    }

    private func loadDemo() {
        reviewMesh = .demo(); reviewID=UUID(); triangleCount=reviewMesh?.triangles.count ?? 0; isReviewing=true
        status="Synthetic 300 × 300 mm surface. Test saving, sharing, and importing; this is not a real scan."
    }

    func prepareCamera() {
        guard mode != .demo, !isReviewing, !isCapturing, sceneIsActive else { return }
        guard ARWorldTrackingConfiguration.isSupported else {
            errorMessage="This device cannot run the AR camera. Demo export still works."
            return
        }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: runCamera()
        case .notDetermined:
            guard !cameraRequestPending else { return }
            cameraRequestPending=true
            AVCaptureDevice.requestAccess(for: .video) { [weak self] allowed in
                DispatchQueue.main.async {
                    guard let self else { return }; self.cameraRequestPending=false
                    if allowed { self.prepareCamera() }
                    else { self.errorMessage="Camera access was declined. Enable it in Settings to scan. Demo mode remains available." }
                }
            }
        default: errorMessage="Camera access is off. Enable Rockform Capture in Settings → Privacy & Security → Camera."
        }
    }

    private func runCamera() {
        guard mode != .demo, !isReviewing, !isCapturing, sceneIsActive else { return }
        let config=ARWorldTrackingConfiguration()
        config.planeDetection=[] // Avoid flattening rock relief using detected planes.
        config.environmentTexturing = .none
        if mode == .lidar {
            config.sceneReconstruction = .mesh
            if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) { config.frameSemantics.insert(.sceneDepth) }
        }
        cameraView.session.run(config, options: [.resetTracking, .removeExistingAnchors])
        cameraReady=true
        status = mode == .lidar ? "Aim at the middle of a small rock patch. Wait for tracking, then start." : "Take reference photos from different angles. Photos do not generate a 3D mesh in this version."
    }

    func startScan() {
        guard mode == .lidar, !isReviewing, cameraReady,
              let current=cameraView.session.currentFrame else { return }
        guard case .normal=current.camera.trackingState else {
            errorMessage="Move slowly until tracking is ready, then start the scan."; return
        }
        guard let distance=centerDepth(current), (0.2...2.0).contains(distance) else {
            errorMessage="Aim at visible rock about 0.2–2 m away until a depth reading appears."; return
        }
        let camera=current.camera.transform
        let position=camera.columns.3.xyz-camera.columns.2.xyz*Float(distance)
        frameTransform=camera
        frameTransform.columns.3=SIMD4(position,1)
        frame=CaptureFrame(origin: Point3(position),right: Point3(camera.columns.0.xyz),
                           up: Point3(camera.columns.1.xyz),outward: Point3(camera.columns.2.xyz))
        overlayRoot.simdTransform=frameTransform
        let box=SCNBox(width:patchWidth,height:patchWidth,length:captureDepthMM/1000,chamferRadius:0)
        let outline=SCNMaterial(); outline.diffuse.contents=UIColor.systemOrange
        outline.fillMode = .lines; outline.lightingModel = .constant; outline.isDoubleSided=true
        box.materials=[outline]; overlayRoot.addChildNode(SCNNode(geometry:box))
        isCapturing=true
        UIApplication.shared.isIdleTimerDisabled=true
        status="Box fixed to this patch. Move slowly sideways and inspect edges from several angles."
        for anchor in current.anchors.compactMap({ $0 as? ARMeshAnchor }) { update(anchor,force: true) }
    }

    func finishCapture(interrupted: Bool = false) {
        guard !isExporting else { return }
        if isCapturing {
            if !interrupted, let current=cameraView.session.currentFrame {
                for anchor in current.anchors.compactMap({ $0 as? ARMeshAnchor }) { update(anchor,force: true) }
            }
            isCapturing=false
            do {
                reviewMesh=try SurfaceMesh.merge(meshes.keys.sorted(by: { $0.uuidString<$1.uuidString }).compactMap { meshes[$0] })
                reviewID=UUID()
            }
            catch { errorMessage=error.localizedDescription }
        }
        cameraView.session.pause(); cameraReady=false; UIApplication.shared.isIdleTimerDisabled=false
        isReviewing = reviewMesh != nil || photoCount>0
        status = interrupted ? "Capture stopped when the app was interrupted. Inspect this result before saving." : "Inspect the surface, enter your reference measurement, then save the ZIP."
        if !isReviewing { status="Nothing captured yet. Start a new scan or take a reference photo." }
    }

    func setActive(_ active: Bool) {
        sceneIsActive=active
        if !active {
            if isCapturing || (mode == .photos && photoCount>0 && !isReviewing) { finishCapture(interrupted: true) }
            cameraView.session.pause(); cameraReady=false; UIApplication.shared.isIdleTimerDisabled=false
        } else if mode != .demo && !isReviewing { prepareCamera() }
    }

    func session(_ session: ARSession, didUpdate current: ARFrame) {
        guard current.timestamp-lastFrameUpdate>0.2 else { return }
        lastFrameUpdate=current.timestamp
        switch current.camera.trackingState {
        case .normal: tracking="Tracking ready"
        case .notAvailable: tracking="Tracking unavailable"
        case .limited(let reason):
            switch reason {
            case .excessiveMotion: tracking="Move more slowly"
            case .insufficientFeatures: tracking="Aim at textured rock"
            case .relocalizing: tracking="Finding position…"
            default: tracking="Starting tracking…"
            }
        }
        rangeMeters=centerDepth(current)
        confidenceText = depthConfidence(current)
    }

    func session(_ session: ARSession, didAdd anchors: [ARAnchor]) {
        for anchor in anchors.compactMap({ $0 as? ARMeshAnchor }) { update(anchor,force: true) }
    }
    func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
        for anchor in anchors.compactMap({ $0 as? ARMeshAnchor }) { update(anchor,force: false) }
    }
    func session(_ session: ARSession, didRemove anchors: [ARAnchor]) {
        guard isCapturing else { return }
        for anchor in anchors { meshes.removeValue(forKey: anchor.identifier); meshNodes.removeValue(forKey: anchor.identifier)?.removeFromParentNode() }
        triangleCount=meshes.values.reduce(0) { $0+$1.triangles.count }
    }
    func sessionWasInterrupted(_ session: ARSession) { if isCapturing { finishCapture(interrupted: true) } }
    func session(_ session: ARSession, didFailWithError error: Error) {
        if isCapturing { finishCapture(interrupted: true) }
        cameraReady=false; errorMessage="Camera session stopped: \(error.localizedDescription). Start a new scan to retry."
    }

    private func update(_ anchor: ARMeshAnchor, force: Bool) {
        guard isCapturing, let frame else { return }
        let now=ProcessInfo.processInfo.systemUptime
        if !force, now-(lastUpdates[anchor.identifier] ?? 0)<0.35 { return }
        lastUpdates[anchor.identifier]=now
        let geometry=anchor.geometry
        guard geometry.vertices.format == .float3, geometry.faces.indexCountPerPrimitive == 3,
              [2,4].contains(geometry.faces.bytesPerIndex) else { return }
        let source=geometry.vertices
        guard source.count<=500_000, geometry.faces.count<=1_000_000 else { stopForSize(); return }
        var points: [Point3]=[]; points.reserveCapacity(source.count)
        for index in 0..<source.count {
            let pointer=UnsafeRawPointer(source.buffer.contents()).advanced(by: source.offset+source.stride*index)
            // ARKit positions may be 12-byte packed; SIMD3<Float> is 16 bytes and
            // cannot safely be loaded directly from a potentially unaligned buffer.
            let local=SIMD4(pointer.loadUnaligned(as: Float.self),pointer.advanced(by:4).loadUnaligned(as: Float.self),
                            pointer.advanced(by:8).loadUnaligned(as: Float.self),1)
            points.append(frame.millimeters(from: Point3((anchor.transform*local).xyz)))
        }
        let faces=geometry.faces
        let pointer=UnsafeRawPointer(faces.buffer.contents())
        func readIndex(_ i: Int) -> Int {
            let p=pointer.advanced(by: i*faces.bytesPerIndex)
            return faces.bytesPerIndex == 4 ? Int(p.loadUnaligned(as: UInt32.self)) : Int(p.loadUnaligned(as: UInt16.self))
        }
        var triangles: [Triangle]=[]; triangles.reserveCapacity(faces.count)
        for i in 0..<faces.count { triangles.append(.init(readIndex(3*i),readIndex(3*i+1),readIndex(3*i+2))) }
        let cropped=SurfaceMesh(vertices:points,triangles:triangles).cropped(widthMM:patchWidth*1000,depthMM:captureDepthMM)
        let totalFaces=meshes.filter { $0.key != anchor.identifier }.values.reduce(0) { $0+$1.triangles.count }+cropped.triangles.count
        let totalVertices=meshes.filter { $0.key != anchor.identifier }.values.reduce(0) { $0+$1.vertices.count }+cropped.vertices.count
        guard totalFaces<=1_000_000,totalVertices<=500_000 else { stopForSize(); return }
        meshes[anchor.identifier]=cropped
        meshNodes.removeValue(forKey: anchor.identifier)?.removeFromParentNode()
        if !cropped.triangles.isEmpty {
            let node=SCNNode(geometry: Self.sceneGeometry(cropped,wireframe:true))
            overlayRoot.addChildNode(node); meshNodes[anchor.identifier]=node
        }
        triangleCount=totalFaces
    }

    private func stopForSize() {
        // Avoid recursion through finishCapture's final anchor update.
        finishCapture(interrupted: true)
        errorMessage=CaptureError.tooLarge.localizedDescription
    }

    private func centerDepth(_ current: ARFrame) -> Double? {
        guard let buffer=current.sceneDepth?.depthMap,
              CVPixelBufferGetPixelFormatType(buffer)==kCVPixelFormatType_DepthFloat32 else { return nil }
        CVPixelBufferLockBaseAddress(buffer,.readOnly); defer { CVPixelBufferUnlockBaseAddress(buffer,.readOnly) }
        guard let base=CVPixelBufferGetBaseAddress(buffer) else { return nil }
        let x=CVPixelBufferGetWidth(buffer)/2,y=CVPixelBufferGetHeight(buffer)/2,stride=CVPixelBufferGetBytesPerRow(buffer)
        let value=Double(base.advanced(by:y*stride+x*4).loadUnaligned(as:Float.self))
        return value.isFinite && value>0 ? value : nil
    }

    private func depthConfidence(_ current: ARFrame) -> String {
        guard let buffer=current.sceneDepth?.confidenceMap else { return "Depth confidence unavailable" }
        CVPixelBufferLockBaseAddress(buffer,.readOnly); defer { CVPixelBufferUnlockBaseAddress(buffer,.readOnly) }
        guard let base=CVPixelBufferGetBaseAddress(buffer) else { return "Depth confidence unavailable" }
        let x=CVPixelBufferGetWidth(buffer)/2,y=CVPixelBufferGetHeight(buffer)/2
        let value=base.advanced(by:y*CVPixelBufferGetBytesPerRow(buffer)+x).load(as:UInt8.self)
        return "Center depth: \(value>=2 ? "high" : value==1 ? "medium" : "low") confidence"
    }

    func takePhoto() {
        guard cameraReady,!isReviewing,let current=cameraView.session.currentFrame else { return }
        guard referencePhotos.count<20 else { errorMessage="The lightweight capture limit is 20 reference photos."; return }
        var image=CIImage(cvPixelBuffer:current.capturedImage).oriented(.right)
        let factor=min(1,1600/max(image.extent.width,image.extent.height))
        image=image.transformed(by:CGAffineTransform(scaleX:factor,y:factor))
        guard let data=imageContext.jpegRepresentation(of:image,colorSpace:CGColorSpaceCreateDeviceRGB(),options:[:]) else {
            errorMessage="Could not encode the reference photo."; return
        }
        let c=current.camera
        referencePhotos.append(.init(jpeg:data,timestamp:current.timestamp,cameraTransform:c.transform.array,intrinsics:c.intrinsics.array))
        photoCount=referencePhotos.count
        status="Reference photo \(photoCount) saved in this capture."
    }

    func saveCapture() {
        guard canExport else { return }
        let normalized=referenceMM.trimmingCharacters(in:.whitespacesAndNewlines).replacingOccurrences(of:",",with:".")
        let reference=normalized.isEmpty ? nil : Double(normalized)
        guard normalized.isEmpty || (reference != nil && reference!.isFinite && reference!>0) else {
            errorMessage="Enter a positive reference measurement in millimeters, or leave it blank."; return
        }
        let mesh=reviewMesh, photos=referencePhotos
        let kind=mode == .demo ? "synthetic-demo" : mode == .lidar && mesh != nil ? "lidar" : "photo-reference-only"
        let report=CaptureReport(kind:kind,mesh:mesh,frame:frame,width:mode == .demo ? 300:patchWidth*1000,
                                 depth:captureDepthMM,reference:reference,referenceDescription:referenceDescription,notes:notes,
                                 device:UIDevice.current.model,systemVersion:UIDevice.current.systemVersion,photoCount:photos.count)
        let cancellation=ExportCancellation(); exportCancellation=cancellation
        isExporting=true; status="Saving capture…"
        DispatchQueue.global(qos:.userInitiated).async { [weak self] in
            do {
                let directory=try Self.exportsDirectory()
                let formatter=DateFormatter(); formatter.dateFormat="yyyyMMdd-HHmmss"; formatter.timeZone=TimeZone(secondsFromGMT:0)
                let url=directory.appendingPathComponent("\(kind)-\(formatter.string(from:Date()))-\(UUID().uuidString.prefix(6)).zip")
                var entries=[ArchiveEntry("capture.json",try report.json())]
                if let mesh { entries.append(.init("surface-mm.obj",try mesh.obj(isDemo:kind == "synthetic-demo",cancelled:{ cancellation.isCancelled }))) }
                struct PhotoInfo: Codable { let file: String; let timestamp: Double; let cameraTransformColumnMajor: [Float]; let intrinsicsColumnMajor: [Float] }
                var records: [PhotoInfo]=[]
                for (i,photo) in photos.enumerated() {
                    let name=String(format:"reference/photo-%03d.jpg",i+1)
                    entries.append(.init(name,photo.jpeg))
                    records.append(.init(file:name,timestamp:photo.timestamp,cameraTransformColumnMajor:photo.cameraTransform,intrinsicsColumnMajor:photo.intrinsics))
                }
                if !records.isEmpty {
                    entries.append(.init("reference/poses.json",try JSONEncoder().encode(records)))
                    entries.append(.init("reference/README.txt",Data("Reference JPEGs are rotated to portrait and reduced to 1600 px. Camera intrinsics describe the ORIGINAL ARKit image, not the resized JPEG. Poses use ARKit world meters; timestamps are session seconds. These files are references, not a calibrated photogrammetry dataset.\n".utf8)))
                }
                entries.append(.init("OPEN-IN-ROCKFORM.txt",Data("Unzip this archive on Windows. If present, import surface-mm.obj into Rockform and select Millimeters. Confirm scale with a physical reference. capture.json records capture settings. A synthetic-demo is not a real scan. Photo-only archives have no mesh. The scan is an open surface; extract a hold and add a back in Rockform.\n".utf8)))
                try CaptureArchive.write(entries,to:url,cancelled:{ cancellation.isCancelled })
                DispatchQueue.main.async {
                    guard let self else { return }; self.isExporting=false; self.exportCancellation=nil
                    self.status="Saved locally. Share the ZIP or find it in Files → On My iPhone → Rockform Capture → Exports."
                    self.refreshLibrary(); self.shareURL=url
                }
            } catch {
                DispatchQueue.main.async { self?.isExporting=false; self?.exportCancellation=nil; self?.errorMessage=error.localizedDescription }
            }
        }
    }

    func cancelExport() { exportCancellation?.cancel(); status="Cancelling export…" }
    func refreshLibrary() {
        guard let directory=try? Self.exportsDirectory() else { return }
        savedFiles=((try? FileManager.default.contentsOfDirectory(at:directory,includingPropertiesForKeys:nil)) ?? [])
            .filter { $0.pathExtension=="zip" }.sorted { $0.lastPathComponent>$1.lastPathComponent }
    }
    private nonisolated static func exportsDirectory() throws -> URL {
        let directory=try FileManager.default.url(for:.documentDirectory,in:.userDomainMask,appropriateFor:nil,create:true).appendingPathComponent("Exports",isDirectory:true)
        try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
        return directory
    }
    static func sceneGeometry(_ mesh: SurfaceMesh,wireframe: Bool) -> SCNGeometry {
        let points=mesh.vertices.map { SCNVector3(Float($0.x/1000),Float($0.y/1000),Float($0.z/1000)) }
        let indices=mesh.triangles.flatMap { [Int32($0.a),Int32($0.b),Int32($0.c)] }
        let data=indices.withUnsafeBufferPointer { Data(buffer:$0) }
        let geometry=SCNGeometry(sources:[SCNGeometrySource(vertices:points)],elements:[SCNGeometryElement(data:data,primitiveType:.triangles,primitiveCount:mesh.triangles.count,bytesPerIndex:4)])
        let material=SCNMaterial(); material.diffuse.contents=UIColor(red:0.32,green:0.83,blue:0.71,alpha:1)
        material.isDoubleSided=true; material.fillMode=wireframe ? .lines:.fill
        material.lightingModel=wireframe ? .constant:.lambert; material.transparency=wireframe ? 0.6:1
        geometry.materials=[material]; return geometry
    }
}

private extension SIMD4 where Scalar == Float { var xyz: SIMD3<Float> { .init(x,y,z) } }
private extension Point3 { init(_ v: SIMD3<Float>) { self.init(Double(v.x),Double(v.y),Double(v.z)) } }
private extension simd_float4x4 { var array: [Float] { [columns.0,columns.1,columns.2,columns.3].flatMap { [$0.x,$0.y,$0.z,$0.w] } } }
private extension simd_float3x3 { var array: [Float] { [columns.0,columns.1,columns.2].flatMap { [$0.x,$0.y,$0.z] } } }
