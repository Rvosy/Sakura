import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

enum CaptureError: Error, CustomStringConvertible {
    case missingArgument(String)
    case invalidArgument(String)
    case noDisplay
    case cannotAddWriterInput
    case captureFailed(String)
    case noAudioSamples

    var description: String {
        switch self {
        case .missingArgument(let name):
            return "missing argument: \(name)"
        case .invalidArgument(let name):
            return "invalid argument: \(name)"
        case .noDisplay:
            return "no display available for ScreenCaptureKit"
        case .cannotAddWriterInput:
            return "cannot add AVAssetWriter audio input"
        case .captureFailed(let message):
            return message
        case .noAudioSamples:
            return "no system audio samples were captured"
        }
    }
}

struct Arguments {
    var output: URL
    var duration: TimeInterval = 3.0
    var sampleRate: Int = 16_000
    var channels: Int = 1
    var excludeCurrentProcess: Bool = true

    static func parse(_ raw: [String]) throws -> Arguments {
        var output: URL?
        var duration = 3.0
        var sampleRate = 16_000
        var channels = 1
        var excludeCurrentProcess = false
        var index = 0
        while index < raw.count {
            let item = raw[index]
            switch item {
            case "--output":
                index += 1
                guard index < raw.count else { throw CaptureError.missingArgument("--output") }
                output = URL(fileURLWithPath: raw[index])
            case "--duration":
                index += 1
                guard index < raw.count, let value = Double(raw[index]) else {
                    throw CaptureError.invalidArgument("--duration")
                }
                duration = max(0.5, min(10.0, value))
            case "--sample-rate":
                index += 1
                guard index < raw.count, let value = Int(raw[index]) else {
                    throw CaptureError.invalidArgument("--sample-rate")
                }
                sampleRate = max(8_000, min(48_000, value))
            case "--channels":
                index += 1
                guard index < raw.count, let value = Int(raw[index]) else {
                    throw CaptureError.invalidArgument("--channels")
                }
                channels = max(1, min(2, value))
            case "--exclude-current-process":
                excludeCurrentProcess = true
            default:
                throw CaptureError.invalidArgument(item)
            }
            index += 1
        }
        guard let output else { throw CaptureError.missingArgument("--output") }
        return Arguments(
            output: output,
            duration: duration,
            sampleRate: sampleRate,
            channels: channels,
            excludeCurrentProcess: excludeCurrentProcess
        )
    }
}

@available(macOS 13.0, *)
final class AudioCaptureOutput: NSObject, SCStreamOutput, SCStreamDelegate {
    private let writer: AVAssetWriter
    private let input: AVAssetWriterInput
    private var sessionStarted = false
    private(set) var audioSampleCount = 0
    private(set) var stoppedError: Error?

    init(outputURL: URL, sampleRate: Int, channels: Int) throws {
        try? FileManager.default.removeItem(at: outputURL)
        writer = try AVAssetWriter(outputURL: outputURL, fileType: .wav)
        input = AVAssetWriterInput(
            mediaType: .audio,
            outputSettings: [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: sampleRate,
                AVNumberOfChannelsKey: channels,
                AVLinearPCMBitDepthKey: 16,
                AVLinearPCMIsFloatKey: false,
                AVLinearPCMIsBigEndianKey: false,
                AVLinearPCMIsNonInterleaved: false,
            ]
        )
        input.expectsMediaDataInRealTime = true
        guard writer.canAdd(input) else { throw CaptureError.cannotAddWriterInput }
        writer.add(input)
        guard writer.startWriting() else {
            throw CaptureError.captureFailed(writer.error?.localizedDescription ?? "AVAssetWriter failed to start")
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        stoppedError = error
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio else { return }
        guard CMSampleBufferDataIsReady(sampleBuffer) else { return }
        if !sessionStarted {
            writer.startSession(atSourceTime: CMSampleBufferGetPresentationTimeStamp(sampleBuffer))
            sessionStarted = true
        }
        guard input.isReadyForMoreMediaData else { return }
        if input.append(sampleBuffer) {
            audioSampleCount += CMSampleBufferGetNumSamples(sampleBuffer)
        }
    }

    func finish() async throws {
        input.markAsFinished()
        await withCheckedContinuation { continuation in
            writer.finishWriting {
                continuation.resume()
            }
        }
        if let error = writer.error {
            throw CaptureError.captureFailed(error.localizedDescription)
        }
        if let stoppedError {
            throw CaptureError.captureFailed(stoppedError.localizedDescription)
        }
        if audioSampleCount <= 0 {
            throw CaptureError.noAudioSamples
        }
    }
}

@available(macOS 13.0, *)
func captureSystemAudio(arguments: Arguments) async throws {
    let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
    guard let display = content.displays.first else { throw CaptureError.noDisplay }
    let filter = SCContentFilter(
        display: display,
        including: content.applications,
        exceptingWindows: []
    )
    let configuration = SCStreamConfiguration()
    configuration.width = 2
    configuration.height = 2
    configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
    configuration.queueDepth = 3
    configuration.capturesAudio = true
    configuration.excludesCurrentProcessAudio = arguments.excludeCurrentProcess
    configuration.sampleRate = arguments.sampleRate
    configuration.channelCount = arguments.channels

    let output = try AudioCaptureOutput(
        outputURL: arguments.output,
        sampleRate: arguments.sampleRate,
        channels: arguments.channels
    )
    let stream = SCStream(filter: filter, configuration: configuration, delegate: output)
    try stream.addStreamOutput(output, type: .audio, sampleHandlerQueue: DispatchQueue(label: "sakura.system-audio"))
    try await stream.startCapture()
    try await Task.sleep(nanoseconds: UInt64(arguments.duration * 1_000_000_000))
    try await stream.stopCapture()
    try await output.finish()
}

@main
struct Main {
    static func main() async {
        do {
            let arguments = try Arguments.parse(Array(CommandLine.arguments.dropFirst()))
            if #available(macOS 13.0, *) {
                try await captureSystemAudio(arguments: arguments)
            } else {
                throw CaptureError.captureFailed("ScreenCaptureKit audio capture requires macOS 13 or newer")
            }
        } catch {
            FileHandle.standardError.write((String(describing: error) + "\n").data(using: .utf8)!)
            exit(1)
        }
    }
}
