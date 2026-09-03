import { ProductApp } from "@/components/product-app";

export default async function ExperimentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ProductApp page="detail" experimentId={id} />;
}
