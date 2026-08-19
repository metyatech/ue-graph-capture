using UnrealBuildTool;

public class UEGraphCapture : ModuleRules
{
    public UEGraphCapture(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
#if UE_5_2_OR_LATER
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
#endif

        PrivateDependencyModuleNames.AddRange(
            new string[]
            {
                "Core",
                "CoreUObject",
                "Engine",
                "Projects",
                "UnrealEd",
                "Kismet",
                "BlueprintGraph",
                "GraphEditor",
                "Slate",
                "SlateCore",
                "Json",
                "JsonUtilities",
                "GraphPrinterGlobals",
                "WidgetPrinter",
                "GenericGraphPrinter"
            }
        );
    }
}
