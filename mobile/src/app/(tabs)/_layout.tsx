import { Tabs } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import type { ColorValue } from "react-native";
import { colors } from "@/lib/theme";

type IconName = keyof typeof Ionicons.glyphMap;
const icon = (name: IconName) => function TabIcon({ color, size }: { color: ColorValue; size: number }) {
  return <Ionicons name={name} size={size} color={color as string} />;
};

export default function TabsLayout() {
  return (
    <Tabs screenOptions={{
      headerStyle: { backgroundColor: colors.canvas },
      headerTintColor: colors.text,
      headerTitleStyle: { fontWeight: "800" },
      headerShadowVisible: false,
      tabBarStyle: { backgroundColor: colors.canvas, borderTopColor: colors.border },
      tabBarActiveTintColor: colors.accent,
      tabBarInactiveTintColor: colors.muted,
      sceneStyle: { backgroundColor: colors.canvas },
    }}>
      <Tabs.Screen name="index" options={{ title: "Predictions", tabBarIcon: icon("football-outline") }} />
      <Tabs.Screen name="daily" options={{ title: "Daily odds", tabBarIcon: icon("flame-outline") }} />
      <Tabs.Screen name="tickets" options={{ title: "My tickets", tabBarIcon: icon("ticket-outline") }} />
      <Tabs.Screen name="account" options={{ title: "Account", tabBarIcon: icon("person-circle-outline") }} />
    </Tabs>
  );
}
