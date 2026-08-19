#pragma once

#include "Containers/Ticker.h"
#include "Modules/ModuleManager.h"

class SGraphEditor;
class UEdGraph;

class FUEGraphCaptureModule final : public IModuleInterface
{
public:
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;

private:
    bool Tick(float DeltaTime);
    void ProcessRequest();
    void StartGraphPrinterCapture();
    void WriteResultAndExit(const TSharedRef<class FJsonObject>& Result);
    void WriteErrorAndExit(const FString& Message);

    FString RequestPath;
    FTSTicker::FDelegateHandle TickHandle;
    bool bRequestStarted = false;
    bool bWaitingForPng = false;
    FString ResultPath;
    FString OutputDirectory;
    FString AssetPath;
    FString GraphName;
    FString GraphType;
    FString GraphPrinterVersion;
    FString GraphPrinterRevision = TEXT("9c42bba098926c2066cf52877909d9b3ccd26d9f");
    TSharedPtr<SGraphEditor> PendingGraphEditor;
    UEdGraph* PendingGraph = nullptr;
    bool bPrintPending = false;
    double RequestedScale = 1.0;
    double RequestedPadding = 100.0;
    bool bRequestedDrawOnlyGraph = true;
    double CaptureStartedSeconds = 0.0;
    double CaptureDeadlineSeconds = 0.0;
};
