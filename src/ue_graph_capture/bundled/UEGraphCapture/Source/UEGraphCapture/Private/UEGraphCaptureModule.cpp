#include "UEGraphCaptureModule.h"

#include "Containers/Ticker.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphSchema.h"
#include "Engine/Blueprint.h"
#include "GenericGraphPrinter/Types/PrintGraphOptions.h"
#include "GenericGraphPrinter/WidgetPrinters/GenericGraphPrinter.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMisc.h"
#include "Interfaces/IPluginManager.h"
#include "BlueprintEditorModule.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Logging/LogMacros.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "GraphEditor.h"
#include "UObject/Package.h"
#include "WidgetPrinter/Types/PrintWidgetOptions.h"

#define LOCTEXT_NAMESPACE "UEGraphCapture"

DEFINE_LOG_CATEGORY_STATIC(LogUEGraphCapture, Log, All);

namespace
{
    const FString MakeGraphTypeName(const UEdGraph* Graph)
    {
        if (!Graph || !Graph->GetSchema())
        {
            return TEXT("Unknown");
        }

        switch (Graph->GetSchema()->GetGraphType(Graph))
        {
        case GT_Ubergraph:
            return TEXT("EventGraph");
        case GT_Function:
            return TEXT("Function");
        case GT_Macro:
            return TEXT("Macro");
        default:
            return TEXT("Other");
        }
    }

    TSharedRef<FJsonObject> MakeGraphJson(const UEdGraph* Graph)
    {
        TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
        Item->SetStringField(TEXT("name"), Graph ? Graph->GetName() : TEXT(""));
        Item->SetStringField(TEXT("type"), MakeGraphTypeName(Graph));
        return Item;
    }

    TArray<TSharedPtr<FJsonValue>> GraphArray(const TArray<UEdGraph*>& Graphs)
    {
        TArray<TSharedPtr<FJsonValue>> Values;
        for (const UEdGraph* Graph : Graphs)
        {
            Values.Add(TSharedPtr<FJsonValue>(MakeShared<FJsonValueObject>(MakeGraphJson(Graph))));
        }
        return Values;
    }

}

void FUEGraphCaptureModule::StartupModule()
{
    if (FParse::Value(FCommandLine::Get(), TEXT("UEGraphCaptureRequest="), RequestPath))
    {
        UE_LOG(LogUEGraphCapture, Display, TEXT("Request received: %s"), *RequestPath);
        TickHandle = FTSTicker::GetCoreTicker().AddTicker(
            FTickerDelegate::CreateRaw(this, &FUEGraphCaptureModule::Tick), 0.0f
        );
    }
}

void FUEGraphCaptureModule::ShutdownModule()
{
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
}

bool FUEGraphCaptureModule::Tick(float DeltaTime)
{
    if (!bRequestStarted)
    {
        bRequestStarted = true;
        ProcessRequest();
        return true;
    }

    if (bPrintPending)
    {
        if (!PendingGraphEditor.IsValid() || PendingGraph == nullptr)
        {
            bPrintPending = false;
            WriteErrorAndExit(TEXT("Blueprint graph editor was lost before GraphPrinter started."));
            return false;
        }

        PendingGraphEditor->SelectAllNodes();
        FSlateRect GraphBounds;
        if (!PendingGraphEditor->GetBoundsForSelectedNodes(GraphBounds, 100.0f))
        {
            if (FPlatformTime::Seconds() >= CaptureDeadlineSeconds)
            {
                bPrintPending = false;
                WriteErrorAndExit(TEXT("Blueprint graph widgets were not laid out before the capture deadline."));
                return false;
            }
            return true;
        }

        bPrintPending = false;
        StartGraphPrinterCapture();
    }

    if (bWaitingForPng)
    {
        TArray<FString> Files;
        IFileManager::Get().FindFiles(Files, *(OutputDirectory / TEXT("*.png")), true, false);
        if (Files.Num() == 1)
        {
            const FString OutputPath = FPaths::Combine(OutputDirectory, Files[0]);
            if (IFileManager::Get().FileSize(*OutputPath) > 0)
            {
                TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
                Result->SetBoolField(TEXT("ok"), true);
                Result->SetStringField(TEXT("action"), TEXT("capture"));
                Result->SetStringField(TEXT("output"), OutputPath);
                Result->SetStringField(TEXT("asset"), AssetPath);
                Result->SetStringField(TEXT("graph"), GraphName);
                Result->SetStringField(TEXT("graphType"), GraphType);
                Result->SetStringField(TEXT("unrealVersion"), FEngineVersion::Current().ToString());
                Result->SetStringField(TEXT("graphPrinterVersion"), GraphPrinterVersion);
                Result->SetStringField(TEXT("graphPrinterRevision"), GraphPrinterRevision);
                Result->SetNumberField(TEXT("durationMs"), (FPlatformTime::Seconds() - CaptureStartedSeconds) * 1000.0);
                WriteResultAndExit(Result);
                return false;
            }
        }
        if (FPlatformTime::Seconds() >= CaptureDeadlineSeconds)
        {
            WriteErrorAndExit(TEXT("GraphPrinter did not produce exactly one PNG before the capture deadline."));
            return false;
        }
        return true;
    }
    return false;
}

void FUEGraphCaptureModule::ProcessRequest()
{
    UE_LOG(LogUEGraphCapture, Display, TEXT("Processing request."));
    FString RequestText;
    if (!FFileHelper::LoadFileToString(RequestText, *RequestPath))
    {
        WriteErrorAndExit(FString::Printf(TEXT("Unable to read request JSON: %s"), *RequestPath));
        return;
    }

    TSharedPtr<FJsonObject> Request;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(RequestText);
    if (!FJsonSerializer::Deserialize(Reader, Request) || !Request.IsValid())
    {
        WriteErrorAndExit(TEXT("Request JSON is invalid."));
        return;
    }

    FString Action;
    FString RequestedAsset;
    FString RequestedGraph;
    if (!Request->TryGetStringField(TEXT("action"), Action)
        || !Request->TryGetStringField(TEXT("asset"), RequestedAsset))
    {
        WriteErrorAndExit(TEXT("Request JSON must contain action and asset."));
        return;
    }
    Request->TryGetStringField(TEXT("graph"), RequestedGraph);
    if (!Request->TryGetStringField(TEXT("resultPath"), ResultPath))
    {
        WriteErrorAndExit(TEXT("Request JSON must contain resultPath."));
        return;
    }
    if (!Request->TryGetStringField(TEXT("outputDirectory"), OutputDirectory))
    {
        WriteErrorAndExit(TEXT("Request JSON must contain outputDirectory."));
        return;
    }

    AssetPath = RequestedAsset;
    GraphName = RequestedGraph;
    Request->TryGetNumberField(TEXT("scale"), RequestedScale);
    Request->TryGetNumberField(TEXT("padding"), RequestedPadding);
    Request->TryGetBoolField(TEXT("drawOnlyGraph"), bRequestedDrawOnlyGraph);
    FPaths::NormalizeFilename(ResultPath);
    FPaths::NormalizeDirectoryName(OutputDirectory);
    IFileManager::Get().MakeDirectory(*OutputDirectory, true);

    UObject* LoadedObject = StaticLoadObject(UBlueprint::StaticClass(), nullptr, *AssetPath);
    UBlueprint* Blueprint = Cast<UBlueprint>(LoadedObject);
    if (!Blueprint)
    {
        WriteErrorAndExit(FString::Printf(TEXT("Blueprint asset could not be loaded: %s"), *AssetPath));
        return;
    }

    UE_LOG(LogUEGraphCapture, Display, TEXT("Loaded Blueprint: %s"), *AssetPath);

    TArray<UEdGraph*> Graphs;
    Blueprint->GetAllGraphs(Graphs);
    if (Action.Equals(TEXT("listGraphs"), ESearchCase::CaseSensitive))
    {
        UE_LOG(LogUEGraphCapture, Display, TEXT("Returning %d graphs."), Graphs.Num());
        TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetStringField(TEXT("action"), Action);
        Result->SetStringField(TEXT("asset"), AssetPath);
        Result->SetArrayField(TEXT("graphs"), GraphArray(Graphs));
        Result->SetStringField(TEXT("unrealVersion"), FEngineVersion::Current().ToString());
        WriteResultAndExit(Result);
        return;
    }

    if (!Action.Equals(TEXT("capture"), ESearchCase::CaseSensitive))
    {
        WriteErrorAndExit(FString::Printf(TEXT("Unsupported request action: %s"), *Action));
        return;
    }

    UEdGraph* TargetGraph = nullptr;
    for (UEdGraph* Candidate : Graphs)
    {
        if (Candidate && Candidate->GetName().Equals(GraphName, ESearchCase::CaseSensitive))
        {
            TargetGraph = Candidate;
            break;
        }
    }
    if (!TargetGraph)
    {
        TArray<FString> Available;
        for (const UEdGraph* Candidate : Graphs)
        {
            if (Candidate)
            {
                Available.Add(Candidate->GetName());
            }
        }
        WriteErrorAndExit(
            FString::Printf(
                TEXT("Graph not found for asset %s. Requested graph: %s. Available graph names: %s"),
                *AssetPath,
                *GraphName,
                *FString::Join(Available, TEXT(", "))
            )
        );
        return;
    }

    TSharedPtr<IBlueprintEditor> BlueprintEditor = FKismetEditorUtilities::GetIBlueprintEditorForObject(Blueprint, true);
    if (!BlueprintEditor.IsValid())
    {
        WriteErrorAndExit(TEXT("Blueprint Editor could not be opened."));
        return;
    }
    UE_LOG(LogUEGraphCapture, Display, TEXT("Blueprint editor opened."));
    TSharedPtr<SGraphEditor> GraphEditor = BlueprintEditor->OpenGraphAndBringToFront(TargetGraph, true);
    if (!GraphEditor.IsValid())
    {
        WriteErrorAndExit(FString::Printf(TEXT("Blueprint graph editor could not be opened for graph: %s"), *GraphName));
        return;
    }
    UE_LOG(LogUEGraphCapture, Display, TEXT("Graph editor opened: %s"), *GraphName);

    PendingGraph = TargetGraph;
    PendingGraphEditor = GraphEditor;
    CaptureStartedSeconds = FPlatformTime::Seconds();
    CaptureDeadlineSeconds = CaptureStartedSeconds + 120.0;
    bPrintPending = true;
    return;
}

void FUEGraphCaptureModule::StartGraphPrinterCapture()
{
    UE_LOG(LogUEGraphCapture, Display, TEXT("Graph widgets are laid out; starting GraphPrinter."));

    UGenericGraphPrinter* Printer = NewObject<UGenericGraphPrinter>(GetTransientPackage());
    if (!Printer)
    {
        WriteErrorAndExit(TEXT("GraphPrinter GenericGraphPrinter could not be created."));
        return;
    }
    UPrintWidgetOptions* BaseOptions = Printer->CreateDefaultPrintOptions(
        UPrintWidgetOptions::EPrintScope::All,
        UPrintWidgetOptions::EExportMethod::ImageFile
    );
    UPrintGraphOptions* Options = Cast<UPrintGraphOptions>(BaseOptions);
    if (!Options)
    {
        WriteErrorAndExit(TEXT("GraphPrinter did not return graph print options."));
        return;
    }

    Options->PrintScope = UPrintWidgetOptions::EPrintScope::All;
    Options->ExportMethod = UPrintWidgetOptions::EExportMethod::ImageFile;
    Options->RenderingScale = static_cast<float>(RequestedScale);
    Options->Padding = static_cast<float>(RequestedPadding);
    Options->bDrawOnlyGraph = bRequestedDrawOnlyGraph;
    Options->ImageWriteOptions.Format = EDesiredImageFormat::PNG;
    Options->ImageWriteOptions.bAsync = false;
    Options->ImageWriteOptions.bOverwriteFile = true;
    Options->OutputDirectoryPath = OutputDirectory;
    Options->SearchTarget = PendingGraphEditor.ToSharedRef();

    if (!Printer->CanPrintWidget(Options))
    {
        WriteErrorAndExit(TEXT("GraphPrinter could not print the selected graph widget."));
        return;
    }
    UE_LOG(LogUEGraphCapture, Display, TEXT("GraphPrinter accepted graph: %s"), *GraphName);

    const TSharedPtr<IPlugin> GraphPrinterPlugin = IPluginManager::Get().FindPlugin(TEXT("GraphPrinter"));
    GraphPrinterVersion = GraphPrinterPlugin.IsValid()
        ? GraphPrinterPlugin->GetDescriptor().VersionName
        : TEXT("unknown");
    GraphType = MakeGraphTypeName(PendingGraph);
    UE_LOG(LogUEGraphCapture, Display, TEXT("Calling GraphPrinter.PrintWidget."));
    Printer->PrintWidget(Options);
    UE_LOG(LogUEGraphCapture, Display, TEXT("GraphPrinter.PrintWidget returned."));
    bWaitingForPng = true;
}

void FUEGraphCaptureModule::WriteResultAndExit(const TSharedRef<FJsonObject>& Result)
{
    FString ResultText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&ResultText);
    FJsonSerializer::Serialize(Result, Writer);
    const FString TemporaryPath = ResultPath + TEXT(".tmp");
    if (!FFileHelper::SaveStringToFile(ResultText, *TemporaryPath, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        return;
    }
    IFileManager::Get().Move(*ResultPath, *TemporaryPath, true, true, false, true);
    FPlatformMisc::RequestExit(false);
}

void FUEGraphCaptureModule::WriteErrorAndExit(const FString& Message)
{
    TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("ok"), false);
    Result->SetStringField(TEXT("error"), Message);
    Result->SetStringField(TEXT("asset"), AssetPath);
    Result->SetStringField(TEXT("graph"), GraphName);
    WriteResultAndExit(Result);
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FUEGraphCaptureModule, UEGraphCapture)
